"""``GET /api/system/info``: the version, the folders in use and the platform."""

from __future__ import annotations

import platform

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from .deps import SettingsDep

router = APIRouter(prefix="/system", tags=["system"])


class SystemInfo(BaseModel):
    version: str
    platform: str
    python: str
    app_data_dir: str
    projects_dir: str
    exports_dir: str
    port: int


@router.get("/info", response_model=SystemInfo)
def system_info(settings: SettingsDep) -> SystemInfo:
    return SystemInfo(
        version=__version__,
        platform=f"{platform.system()} {platform.release()}".strip(),
        python=platform.python_version(),
        app_data_dir=str(settings.app_data_dir),
        projects_dir=str(settings.projects_dir),
        exports_dir=str(settings.exports_dir),
        port=settings.port,
    )
