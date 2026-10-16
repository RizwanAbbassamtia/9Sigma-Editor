"""The render job runner: one render at a time, started from the API, followed by polling.

``RenderRunner.start`` makes a job (``edit/jobs.py``), runs :func:`~.renderer.render_edit`
in a daemon thread and keeps the edit's ``status`` in ``project.json`` in step (rendering,
rendered, failed). ``status_of`` answers ``GET /api/edits/{id}/render/status``; ``cancel``
sets the job's cancel flag, which FFmpeg sees within a second.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from ..models.project import Project
from ..render import ffmpeg as ff
from ..storage.project_store import ProjectStore, ProjectStoreError
from .jobs import Job, JobRegistry
from .renderer import PROXY_ID, RenderFailed, RenderOutcome, read_state, render_edit

log = logging.getLogger(__name__)

PRESET_IN_MESSAGE = re.compile(r"Rendering (the preview|[0-9]{3,4}p)\b", re.IGNORECASE)
PERCENT_IN_MESSAGE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


class RangeRequest(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


class RenderRequest(BaseModel):
    """Body of ``POST /api/edits/{id}/render``."""

    presets: list[str] | None = Field(default=None, description="the sizes to render; "
                                      "missing = the edit's ticked sizes, [] = the preview only")
    quality: str | None = Field(default=None, description="standard, high or best")
    upload_preset: str | None = Field(default=None, description="none, youtube_1080p, "
                                      "youtube_4k")
    h265_copy: bool | None = None
    range: RangeRequest | None = None


class RenderStatus(BaseModel):
    running: bool
    job_id: str | None = None
    preset: str | None = None
    pct: float | None = None
    message: str = ""
    error: str | None = None
    finished_at: str | None = None
    rendered: list[str] = []
    kept: list[str] = []


class RenderBusy(Exception):
    """A render is already running (one at a time on this laptop)."""


@dataclass
class _Running:
    job: Job
    cancel: threading.Event
    project_id: str
    thread: threading.Thread | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_progress(message: str, pct: float | None) -> tuple[str | None, float | None]:
    """``("1080p", 45.0)`` from ``Rendering 1080p 45%``; the preview as ``proxy``."""
    found = PRESET_IN_MESSAGE.search(message or "")
    preset = None
    if found:
        word = found.group(1).lower()
        preset = PROXY_ID if word == "the preview" else word
    value = pct
    if value is None:
        percent = PERCENT_IN_MESSAGE.search(message or "")
        value = float(percent.group(1)) if percent else None
    return preset, (max(0.0, min(100.0, float(value))) if value is not None else None)


class RenderRunner:
    def __init__(
        self, store_of: Callable[[], ProjectStore], settings_of: Callable[[], Any]
    ) -> None:
        self.store_of = store_of
        self.settings_of = settings_of
        self.registry = JobRegistry()
        self._lock = threading.Lock()
        self._running: _Running | None = None
        self._last: dict[str, Job] = {}
        self._outcomes: dict[str, RenderOutcome] = {}

    @property
    def store(self) -> ProjectStore:
        return self.store_of()

    # Questions ---------------------------------------------------------------------------

    def busy(self) -> bool:
        with self._lock:
            return self._running is not None and self._running.job.is_active

    def running_for(self, project_id: str) -> _Running | None:
        with self._lock:
            current = self._running
            if current is not None and current.project_id == project_id and current.job.is_active:
                return current
            return None

    def status_of(self, project_id: str) -> RenderStatus:
        running = self.running_for(project_id)
        if running is not None:
            job = self.registry.get(running.job.id) or running.job
            preset, pct = parse_progress(job.message, job.progress if job.progress > 0 else None)
            return RenderStatus(
                running=True, job_id=job.id, preset=preset, pct=pct, message=job.message
            )
        with self._lock:
            last = self._last.get(project_id)
            outcome = self._outcomes.get(project_id)
        if last is None:
            state = read_state(self._folder_of(project_id))
            last_render = state.get("last_render")
            finished = last_render if isinstance(last_render, dict) else {}
            error = state.get("last_error") if isinstance(state.get("last_error"), str) else None
            return RenderStatus(
                running=False, error=error, finished_at=finished.get("finished_at"),
                rendered=list(finished.get("rendered") or []),
            )
        return RenderStatus(
            running=False,
            job_id=last.id,
            message=last.message,
            error=last.error,
            finished_at=last.updated_at.isoformat(timespec="seconds"),
            rendered=list(outcome.rendered) if outcome else [],
            kept=list(outcome.kept) if outcome else [],
        )

    def running_info(self, project_id: str) -> dict[str, Any]:
        """What ``build_payload`` shows while a render runs."""
        status = self.status_of(project_id)
        return {"running": status.running, "message": status.message, "pct": status.pct}

    def _folder_of(self, project_id: str):
        from pathlib import Path

        try:
            return Path(self.store.get(project_id).folder) / "07_edit"
        except (ProjectStoreError, OSError):
            return Path()

    # Starting and stopping ------------------------------------------------------------------

    def start(self, project: Project, request: RenderRequest) -> Job:
        with self._lock:
            if self._running is not None and self._running.job.is_active:
                other = self._running.project_id
                raise RenderBusy(
                    "Another render is running already"
                    + ("" if other == project.id else " on another edit")
                    + ". Wait for it to finish, or stop it first."
                )
            job = self.registry.create(
                "render", message="Starting", meta={"project_id": project.id}
            )
            running = _Running(job=job, cancel=threading.Event(), project_id=project.id)
            self._running = running
        self._set_status(project.id, "rendering", "")
        thread = self.registry.run_in_thread(
            job.id, lambda current, progress: self._work(project.id, request, running, progress),
            friendly_errors=(RenderFailed, ff.RenderError, ff.RenderCancelled),
        )
        running.thread = thread
        return job

    def cancel(self, project_id: str) -> bool:
        running = self.running_for(project_id)
        if running is None:
            return False
        running.cancel.set()
        return True

    def wait(self, project_id: str, timeout: float = 600.0) -> Job | None:
        """Block until the edit's render ends (tests, the CLI)."""
        running = self.running_for(project_id)
        if running is None:
            return None
        return self.registry.wait(running.job.id, timeout=timeout)

    # The work ----------------------------------------------------------------------------

    def _work(self, project_id: str, request: RenderRequest, running: _Running,
              progress: Callable[[str, float | None], None]) -> dict[str, Any]:
        project = self.store.get(project_id)
        range_s = (request.range.start_s, request.range.end_s) if request.range else None
        try:
            outcome = render_edit(
                project, self.settings_of(),
                presets=request.presets, quality=request.quality,
                upload_preset=request.upload_preset, h265_copy=request.h265_copy,
                range_s=range_s, cancel=running.cancel, report=progress,
            )
        except ff.RenderCancelled as exc:
            self._finish(project_id, None, "Render stopped.", running, stopped=True)
            raise RenderFailed("The render was stopped.") from exc
        except (RenderFailed, ff.RenderError) as exc:
            self._finish(project_id, None, str(exc), running)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded as failed, then re-raised for the log
            self._finish(project_id, None, "The render hit an unexpected problem.", running)
            raise RenderFailed(
                "The render hit an unexpected problem. Open the render log in the editor."
            ) from exc
        self._finish(project_id, outcome, "", running)
        return {
            "rendered": outcome.rendered, "kept": outcome.kept, "warnings": outcome.warnings,
            "notes": outcome.notes, "seconds": round(outcome.seconds, 1),
        }

    def _finish(self, project_id: str, outcome: RenderOutcome | None, note: str,
                running: _Running, *, stopped: bool = False) -> None:
        with self._lock:
            self._last[project_id] = running.job
            if outcome is not None:
                self._outcomes[project_id] = outcome
            if self._running is running:
                self._running = None
        if outcome is not None:
            finals = [p for p in outcome.rendered + outcome.kept if p != PROXY_ID]
            self._set_status(project_id, "rendered" if finals else "ready", "",
                             duration_s=outcome.duration_s)
        elif stopped:
            self._set_status(project_id, "ready", note)
        else:
            self._set_status(project_id, "failed", note)

    def _set_status(self, project_id: str, status: str, note: str,
                    duration_s: float | None = None) -> None:
        try:
            project = self.store.get(project_id)
            project.status = status  # type: ignore[assignment]
            project.status_note = note
            if duration_s is not None:
                project.duration_s = round(float(duration_s), 3)
            self.store.save(project)
        except (ProjectStoreError, OSError) as exc:
            log.warning("The edit's status could not be saved: %s", exc)
