"""``/api/system/update``: is a newer 9Sigma Editor published, and install it with one click.

docs/RELEASE-CONTRACT.md section 5. Any signed-in person may use these. Nothing here waits
for GitHub: the check and the download run in the background and the page polls
``GET /api/system/update``.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from ..updates import BUSY_JOB, BUSY_VIDEO, UpdateInstallError, UpdateManager, UpdateStatus
from .deps import CurrentUser

router = APIRouter(prefix="/system", tags=["system"])

_CREATE_LOCK = threading.Lock()


def busy_reason(app: FastAPI) -> str:
    """Why an update must wait (a render is running), or ``""``."""
    runner: Any = getattr(app.state, "jobs", None)
    if runner is None:
        return ""
    busy = getattr(runner, "busy", None)
    if callable(busy) and busy():
        return BUSY_VIDEO
    registry = getattr(runner, "registry", None)
    if registry is not None and registry.find(active_only=True):
        return BUSY_JOB
    return ""


def get_update_manager(request: Request) -> UpdateManager:
    """The app's one :class:`UpdateManager`, made on first use (tests may set their own on
    ``app.state.update_manager`` first)."""
    app: FastAPI = request.app
    manager = getattr(app.state, "update_manager", None)
    if manager is None:
        with _CREATE_LOCK:
            manager = getattr(app.state, "update_manager", None)
            if manager is None:
                manager = UpdateManager(busy=lambda: busy_reason(app))
                app.state.update_manager = manager
    return manager


@router.get("/update", response_model=UpdateStatus)
def update_status(request: Request, _user: CurrentUser) -> UpdateStatus:
    manager = get_update_manager(request)
    manager.maybe_check()
    return manager.status()


@router.post("/update/check", response_model=UpdateStatus, status_code=202)
def check_for_updates(request: Request, _user: CurrentUser) -> UpdateStatus:
    manager = get_update_manager(request)
    manager.check_now()
    return manager.status()


@router.post("/update/install", response_model=UpdateStatus, status_code=202)
def install_update(request: Request, _user: CurrentUser) -> UpdateStatus:
    manager = get_update_manager(request)
    try:
        return manager.start_install()
    except UpdateInstallError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
