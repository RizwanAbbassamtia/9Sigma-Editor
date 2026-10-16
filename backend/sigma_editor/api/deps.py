"""Request-scoped helpers shared by the routers: the settings, the edit store and the render
jobs. There is no sign-in in this app (docs/CONTRACT-v0.1.md section 0.3), so nothing here
checks who asks; the same-origin guard in ``app.py`` keeps other websites out.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from ..config import Settings
from ..storage.project_store import ProjectStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> ProjectStore:
    return request.app.state.store


def get_jobs(request: Request) -> Any:
    """The app's render job runner (``edit/jobs.py``), kept on ``app.state.jobs``."""
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ProjectStore, Depends(get_store)]
JobsDep = Annotated[Any, Depends(get_jobs)]
