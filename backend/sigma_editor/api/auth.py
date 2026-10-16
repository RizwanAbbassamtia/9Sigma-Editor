"""``/api/auth``: sign in, sign out, who is signed in, change the password.

Five wrong passwords in a row lock an account for 30 seconds (``LoginLimiter``); the
message never says whether the name or the password was wrong.
"""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth.middleware import SIGN_IN_MESSAGE, session_cookie
from ..auth.passwords import PASSWORD_RULE, check_password, verify_password
from ..auth.sessions import SessionStore, clear_session_cookie, set_session_cookie
from ..auth.users import PublicUser, UserStore, UserStoreError, public_of
from .deps import CurrentUser

router = APIRouter(prefix="/api/auth", tags=["auth"])

WRONG_SIGN_IN = "That user name and password do not match."
MAX_FAILURES = 5
LOCK_SECONDS = 30.0


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = Field(default=False, description="Keep me signed in on this laptop")


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AuthStatus(BaseModel):
    auth_required: bool = True
    accounts: int
    signed_in: bool
    user: PublicUser | None = None
    password_rule: str = PASSWORD_RULE


class LoginLimiter:
    """Per account: how many sign-ins failed in a row and until when it is locked."""

    def __init__(self, max_failures: int = MAX_FAILURES, lock_seconds: float = LOCK_SECONDS):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_locked(self, username: str) -> float:
        with self._lock:
            until = self._locked_until.get(username, 0.0)
            remaining = until - time.monotonic()
            return remaining if remaining > 0 else 0.0

    def failed(self, username: str) -> None:
        with self._lock:
            count = self._failures.get(username, 0) + 1
            self._failures[username] = count
            if count >= self.max_failures:
                self._failures[username] = 0
                self._locked_until[username] = time.monotonic() + self.lock_seconds

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)
            self._locked_until.pop(username, None)


def users_of(request: Request) -> UserStore:
    return request.app.state.users


def sessions_of(request: Request) -> SessionStore:
    return request.app.state.sessions


def limiter_of(request: Request) -> LoginLimiter:
    limiter = getattr(request.app.state, "login_limiter", None)
    if limiter is None:
        limiter = LoginLimiter()
        request.app.state.login_limiter = limiter
    return limiter


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    """Public: how many accounts exist and whether this browser is signed in."""
    from ..auth.middleware import resolve_user

    users = users_of(request)
    user = await asyncio.to_thread(resolve_user, request.app, session_cookie(request.scope))
    count = len(await asyncio.to_thread(users.load))
    return AuthStatus(accounts=count, signed_in=user is not None, user=user)


@router.post("/login", response_model=PublicUser)
async def login(body: LoginBody, request: Request, response: Response) -> PublicUser:
    username = body.username.strip().lower()
    limiter = limiter_of(request)
    locked = limiter.seconds_locked(username)
    if locked > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong passwords. Try again in {int(locked) + 1} seconds.",
        )
    user = await asyncio.to_thread(users_of(request).authenticate, username, body.password)
    if user is None:
        limiter.failed(username)
        raise HTTPException(status_code=401, detail=WRONG_SIGN_IN)
    limiter.succeeded(username)
    session = await asyncio.to_thread(
        sessions_of(request).create, user.username, remember=body.remember
    )
    set_session_cookie(response, session)
    return public_of(user)


@router.post("/logout", status_code=204, response_class=Response)
async def logout(request: Request) -> Response:
    token = session_cookie(request.scope)
    await asyncio.to_thread(sessions_of(request).delete, token)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=PublicUser)
async def me(user: CurrentUser) -> PublicUser:
    return user


@router.post("/change-password", response_model=PublicUser)
async def change_password(
    body: ChangePasswordBody, request: Request, user: CurrentUser
) -> PublicUser:
    users = users_of(request)
    account = await asyncio.to_thread(users.get, user.username)
    if account is None:
        raise HTTPException(status_code=401, detail=SIGN_IN_MESSAGE)
    if not verify_password(body.current_password, account.password_hash):
        raise HTTPException(status_code=400, detail="The current password is not right.")
    problem = check_password(body.new_password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    try:
        changed = await asyncio.to_thread(users.set_password, user.username, body.new_password)
    except UserStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Other browsers signed in as this person must sign in again; this one stays.
    await asyncio.to_thread(
        sessions_of(request).delete_for, user.username, keep=session_cookie(request.scope)
    )
    return public_of(changed)
