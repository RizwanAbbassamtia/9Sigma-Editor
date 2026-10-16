"""The sign-in check: every ``/api/*`` request needs a live session, except sign-in itself,
the sign-in status, the system info and the API docs. The app's pages (the single-page
front end) are always served; the page sends people to ``/login`` when ``/api/auth/me``
says nobody is signed in. A WebSocket handshake without a session is closed with 4401.
"""

from __future__ import annotations

from typing import Any

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from .sessions import SESSION_COOKIE, SessionStore
from .users import PublicUser, UserStore, public_of

PUBLIC_PATHS = frozenset(
    {"/api/auth/login", "/api/auth/status", "/api/system/info", "/api/openapi.json"}
)
PUBLIC_PREFIXES = ("/api/docs",)
SIGN_IN_MESSAGE = "Please sign in."
WS_SIGN_IN_CODE = 4401


def session_cookie(scope: Scope) -> str | None:
    cookie_header = Headers(scope=scope).get("cookie")
    if not cookie_header:
        return None
    return cookie_parser(cookie_header).get(SESSION_COOKIE)


def resolve_user(app: Any, token: str | None) -> PublicUser | None:
    """The signed-in person for a session token, or ``None``."""
    sessions: SessionStore | None = getattr(app.state, "sessions", None)
    users: UserStore | None = getattr(app.state, "users", None)
    if sessions is None or users is None:
        return None
    session = sessions.get(token)
    if session is None:
        return None
    user = users.get(session.username)
    if user is None or not user.enabled:
        sessions.delete(token)
        return None
    return public_of(user)


def current_user(scope: Scope) -> PublicUser | None:
    state = scope.get("state") or {}
    user = state.get("user")
    return user if isinstance(user, PublicUser) else None


def is_protected(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return False
    return True


class AuthMiddleware:
    """Pure ASGI middleware; see the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope["type"] == "http":
            preflight = scope.get("method", "GET") == "OPTIONS"
            if preflight or not is_protected(path):
                await self.app(scope, receive, send)
                return
        if scope["type"] == "websocket" and not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        user = resolve_user(scope["app"], session_cookie(scope))
        if user is None:
            if scope["type"] == "websocket":
                close = WebSocketClose(code=WS_SIGN_IN_CODE, reason=SIGN_IN_MESSAGE)
                await close(scope, receive, send)
            else:
                response = JSONResponse(status_code=401, content={"detail": SIGN_IN_MESSAGE})
                await response(scope, receive, send)
            return
        scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)
