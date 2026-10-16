"""FastAPI application factory.

``create_app()`` reads the settings (so tests can set ``SGE_*`` environment variables first),
mounts the API under ``/api`` and serves the built front end from ``frontend/dist`` when
that folder exists, with a single-page-app fallback to ``index.html``.

Errors always come back as JSON ``{detail: ...}``: a validation error lists ``type``, ``loc``
and ``msg`` per problem, and an unexpected exception becomes a plain-English 500 instead of a
bare text response. There is no sign-in: the same-origin guard keeps other websites out.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import __version__
from .api import doctor, edits, system
from .api import settings as settings_api
from .config import Settings, load_settings
from .origin_guard import SameOriginGuard
from .storage.project_store import ProjectStore

log = logging.getLogger(__name__)

# Only the Vite dev server may call the API from another origin.
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]

UNEXPECTED_ERROR_MESSAGE = (
    "Something went wrong inside 9Sigma Editor. Try again; if it keeps happening, restart "
    "the app and run the health checks in Settings."
)

NO_FRONTEND_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>9Sigma Editor</title>
<style>body{font-family:system-ui,sans-serif;background:#0B1220;color:#eee;margin:0;
display:grid;place-items:center;height:100vh}main{max-width:36rem;padding:2rem}
code{background:#1b2436;padding:.1rem .4rem;border-radius:.25rem}</style></head>
<body><main><h1>9Sigma Editor is running</h1>
<p>The app screens have not been built yet. In the <code>frontend</code> folder run
<code>npm install</code> and <code>npm run build</code>, then reload this page.</p>
<p>The API is available under <code>/api</code>.</p></main></body></html>
"""

OPTIONAL_ROUTERS: tuple[tuple[str, str], ...] = (
    ("sigma_editor.api.updates", "/api"),
    ("sigma_editor.api.files", ""),
    ("sigma_editor.api.preview", ""),
    ("sigma_editor.api.editing", ""),
    ("sigma_editor.api.ws", ""),
)
"""Routers that arrive with the render engine; each is mounted when its module imports."""


def frontend_dist_dir() -> Path | None:
    """``frontend/dist`` next to the repo, or ``SGE_FRONTEND_DIST``; ``None`` if not built."""
    override = os.environ.get("SGE_FRONTEND_DIST", "").strip()
    repo_root = Path(__file__).resolve().parents[2]
    candidate = Path(override) if override else repo_root / "frontend" / "dist"
    if (candidate / "index.html").is_file():
        return candidate
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()

    app = FastAPI(
        title="9Sigma Editor",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.store = ProjectStore(settings.projects_dir)
    app.state.jobs = None

    # The guard sits inside CORS: preflights are answered by CORS, every other request that
    # changes something must come from this server's own page or the dev server.
    app.add_middleware(SameOriginGuard, allowed_origins=DEV_ORIGINS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(RequestValidationError, validation_error_response)
    app.add_exception_handler(Exception, unexpected_error_response)

    app.include_router(system.router, prefix="/api")
    app.include_router(doctor.router, prefix="/api")
    app.include_router(settings_api.router, prefix="/api")
    app.include_router(edits.router)
    for module_name, prefix in OPTIONAL_ROUTERS:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            log.info("%s is not available yet: %s", module_name, exc)
            continue
        app.include_router(module.router, prefix=prefix)
        setup = getattr(module, "setup_app", None)
        if callable(setup):
            setup(app)

    dist = frontend_dist_dir()
    if dist is not None:
        _mount_frontend(app, dist)
    else:

        @app.get("/", include_in_schema=False)
        def no_frontend() -> HTMLResponse:
            return HTMLResponse(NO_FRONTEND_PAGE)

    return app


async def validation_error_response(_request: Request, exc: Exception) -> JSONResponse:
    """422 with ``type``, ``loc`` and ``msg`` per error and nothing else."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [
        {
            "type": str(error.get("type", "value_error")),
            "loc": list(error.get("loc", ())),
            "msg": str(error.get("msg", "Invalid value")),
        }
        for error in errors
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


async def unexpected_error_response(_request: Request, _exc: Exception) -> JSONResponse:
    """A JSON 500 in plain English. The traceback still goes to the server log."""
    return JSONResponse(status_code=500, content={"detail": UNEXPECTED_ERROR_MESSAGE})


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path:
            try:
                candidate = (root / full_path).resolve()
                is_asset = candidate.is_file() and root in candidate.parents
            except (OSError, ValueError):
                is_asset = False  # e.g. a NUL byte or an over-long name in the address
            if is_asset:
                return FileResponse(candidate)
        return FileResponse(index)
