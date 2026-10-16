"""Request-scoped helpers shared by the routers: the settings, the edit store, the render
jobs and the signed-in person (docs/CONTRACT-v0.1.md section 0.3). The sign-in middleware
has already refused requests without a session; ``CurrentUser`` reads what it found.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from ..auth.middleware import SIGN_IN_MESSAGE, current_user
from ..auth.users import PublicUser
from ..config import Settings
from ..storage.project_store import ProjectStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_current_user(request: Request) -> PublicUser:
    user = current_user(request.scope)
    if user is None:
        raise HTTPException(status_code=401, detail=SIGN_IN_MESSAGE)
    return user


def get_store(request: Request) -> ProjectStore:
    return request.app.state.store


def get_jobs(request: Request) -> Any:
    """The app's render job runner (``edit/jobs.py``), kept on ``app.state.jobs``."""
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ProjectStore, Depends(get_store)]
JobsDep = Annotated[Any, Depends(get_jobs)]
CurrentUser = Annotated[PublicUser, Depends(get_current_user)]
