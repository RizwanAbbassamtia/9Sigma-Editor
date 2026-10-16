"""Sessions: who is signed in, kept in ``<app_data_dir>/sessions.json`` so a session
survives a restart of the app. A session lasts 12 hours, or 30 days with "Keep me signed in".
The browser holds the session token in an HttpOnly cookie.
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

from ..storage.files import atomic_write_text

SESSIONS_FILE = "sessions.json"
SESSION_COOKIE = "sge_session"
SESSION_HOURS = 12
REMEMBER_DAYS = 30
MAX_SESSIONS = 200
LOCK = threading.RLock()


def utc_now() -> datetime:
    return datetime.now(UTC)


class Session(BaseModel):
    token: str
    username: str
    created_at: datetime
    expires_at: datetime
    remember: bool = False

    @property
    def expired(self) -> bool:
        return self.expires_at <= utc_now()


class SessionsDoc(BaseModel):
    sessions: list[Session] = Field(default=[])


class SessionStore:
    def __init__(self, app_data_dir: Path | str) -> None:
        self.path = Path(app_data_dir) / SESSIONS_FILE
        self._sessions: dict[str, Session] | None = None

    def _load(self) -> dict[str, Session]:
        if self._sessions is None:
            try:
                doc = SessionsDoc.model_validate_json(self.path.read_text(encoding="utf-8"))
                self._sessions = {s.token: s for s in doc.sessions if not s.expired}
            except (OSError, ValidationError, ValueError):
                self._sessions = {}
        return self._sessions

    def _save(self) -> None:
        sessions = self._load()
        live = sorted(
            (s for s in sessions.values() if not s.expired), key=lambda s: s.created_at
        )[-MAX_SESSIONS:]
        self._sessions = {s.token: s for s in live}
        text = SessionsDoc(sessions=live).model_dump_json(indent=2) + "\n"
        try:
            atomic_write_text(self.path, text)
        except OSError:
            pass  # the sessions still work for this run

    def create(self, username: str, *, remember: bool = False) -> Session:
        now = utc_now()
        life = timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)
        session = Session(
            token=secrets.token_urlsafe(32),
            username=username,
            created_at=now,
            expires_at=now + life,
            remember=remember,
        )
        with LOCK:
            self._load()[session.token] = session
            self._save()
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with LOCK:
            session = self._load().get(token)
            if session is None:
                return None
            if session.expired:
                del self._load()[token]
                self._save()
                return None
            return session

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with LOCK:
            if self._load().pop(token, None) is not None:
                self._save()

    def delete_for(self, username: str, *, keep: str | None = None) -> int:
        """End every session of ``username`` except ``keep``; returns how many ended."""
        with LOCK:
            sessions = self._load()
            doomed = [t for t, s in sessions.items() if s.username == username and t != keep]
            for token in doomed:
                del sessions[token]
            if doomed:
                self._save()
            return len(doomed)


def set_session_cookie(response: Response, session: Session) -> None:
    max_age = int((session.expires_at - utc_now()).total_seconds()) if session.remember else None
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # the app only ever runs on http://127.0.0.1
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
