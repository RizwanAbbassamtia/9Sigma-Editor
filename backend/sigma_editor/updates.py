"""In-app updates: find a newer 9Sigma Editor on GitHub and install it with one click.

docs/RELEASE-CONTRACT.md sections 4 and 5. Modelled on the owner's earlier app (SceneForge):

* **Checking** reads the latest GitHub release of :data:`GITHUB_REPO` (public, no token). When
  the API refuses (60 unauthenticated calls an hour per address) the release page's redirect
  still names the newest tag. A check never blocks a request: :class:`UpdateManager` runs it in
  a background thread at most every :data:`CHECK_EVERY_S` (sooner after a failure) and the page
  polls ``GET /api/system/update`` for the answer.
* **Update now** downloads ``Install-9SigmaEditor-<version>.exe`` to the temp folder, refuses
  it unless it is exactly the file the release describes (size, SHA-256 digest when GitHub gives
  one, the payload trailer and the version inside it), starts it in its no-click mode
  (``--auto --open``) with this process id and this install folder, and closes the app so the
  installer can replace the program files. The installer opens the app again when it is done.
* Only a copy set up by the installer can update itself (:func:`install_root`); a developer's
  checkout gets the release page instead.

Messages are plain English: the people reading them are video editors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import BaseModel

from . import __version__

log = logging.getLogger(__name__)

GITHUB_REPO = "RizwanAbbassamtia/9Sigma-Editor"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
WEB_LATEST = f"https://github.com/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
INSTALLER_PREFIX = "Install-9SigmaEditor-"
INSTALLER_RE = re.compile(r"^Install-9SigmaEditor-(?P<version>[0-9][0-9A-Za-z.+\-]*)\.exe$")
INSTALL_INFO = "install-info.json"
PAYLOAD_MAGIC = b"SIGMA9ED"
TRAILER = struct.Struct("<Q8s")
"""The last 16 bytes of the Setup EXE: where the payload zip starts, and the magic."""
PAYLOAD_INFO = "9SigmaEditor/" + INSTALL_INFO

CHECK_EVERY_S = 6 * 60 * 60
RETRY_AFTER_FAILURE_S = 30 * 60
CHECK_TIMEOUT_S = 10.0
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=20.0, read=60.0, write=60.0, pool=20.0)
MAX_INSTALLER_BYTES = 2 * 1024**3
NOTES_LIMIT = 2000
INSTALLER_ARGS = ("--auto", "--open")
EXIT_DELAY_S = 2.0
FORCE_EXIT_AFTER_S = 10.0
DOWNLOAD_DIR_NAME = "9SigmaEditor-update"

UpdateState = Literal[
    "unknown",
    "up_to_date",
    "available",
    "check_failed",
    "downloading",
    "installing",
    "install_failed",
]

NOT_INSTALLED_BLOCKER = (
    "This copy of 9Sigma Editor was not set up with the installer, so it cannot update "
    "itself. Use Download to get the new version."
)
NOT_WINDOWS_BLOCKER = "Updates install themselves on Windows only. Use Download instead."
NO_INSTALLER_BLOCKER = (
    "This release has no installer attached yet. Try again later, or use Download."
)
BUSY_VIDEO = "A video is being made right now. Update when it has finished, so no work is lost."
BUSY_JOB = "Another task is still running. Update when it has finished."


# Versions -------------------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^\s*[vV]?(?P<numbers>\d+(?:\.\d+)*)(?P<rest>.*)$")


def parse_version(text: str | None) -> tuple[tuple[int, ...], str] | None:
    """``(numbers, pre-release tail)`` of ``v1.2.3`` / ``1.2.3rc1`` / ``1.2.3-beta.2``.

    Trailing zeros are dropped (``1.0`` is ``1.0.0``), ``+build`` metadata is ignored, and
    anything that does not start with a number gives ``None``.
    """
    match = _VERSION_RE.match(str(text or ""))
    if not match:
        return None
    numbers = tuple(int(part) for part in match.group("numbers").split("."))
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers = numbers[:-1]
    rest = match.group("rest").split("+", 1)[0].strip().lstrip("-._").lower()
    return numbers, rest


def _natural(text: str) -> tuple[tuple[int, int | str], ...]:
    """``rc10`` after ``rc9``: digits compare as numbers, everything else as text."""
    return tuple(
        (0, int(piece)) if piece.isdigit() else (1, piece) for piece in re.findall(r"\d+|\D+", text)
    )


def compare_versions(a: str | None, b: str | None) -> int | None:
    """-1, 0 or 1 like ``a`` vs ``b``; ``None`` when either is not a version."""
    left, right = parse_version(a), parse_version(b)
    if left is None or right is None:
        return None
    if left[0] != right[0]:
        return -1 if left[0] < right[0] else 1
    tail_a, tail_b = left[1], right[1]
    if tail_a == tail_b:
        return 0
    if not tail_a:
        return 1  # 1.0.0 is newer than 1.0.0rc1
    if not tail_b:
        return -1
    return -1 if _natural(tail_a) < _natural(tail_b) else 1


def is_newer(candidate: str | None, current: str | None) -> bool:
    """True only when ``candidate`` is a version and a later one than ``current``."""
    return compare_versions(candidate, current) == 1


# Releases -------------------------------------------------------------------------------------


class UpdateCheckError(Exception):
    """The check could not be made; the message is plain English for the page."""


class UpdateInstallError(Exception):
    """The update could not be downloaded, checked or started (plain English)."""


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    html_url: str
    notes: str = ""
    published_at: str | None = None
    installer_name: str = ""
    installer_url: str = ""
    installer_size: int = 0
    installer_sha256: str = ""
    """From the asset's ``digest`` (``sha256:<hex>``); empty when GitHub gave none."""


def installer_name_for(version: str) -> str:
    return f"{INSTALLER_PREFIX}{version}.exe"


def _version_of_tag(tag: str) -> str:
    version = str(tag or "").strip()
    if version[:1] in ("v", "V"):
        version = version[1:]
    if parse_version(version) is None:
        raise ValueError(f"the release tag {tag!r} is not a version")
    return version


def _sha256_of_digest(digest: Any) -> str:
    text = str(digest or "").strip().lower()
    if text.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", text[7:]):
        return text[7:]
    return ""


def release_from_api(raw: Any) -> Release:
    """A :class:`Release` from the GitHub API's release object. ``ValueError`` when the answer
    is not a usable release."""
    if not isinstance(raw, dict):
        raise ValueError("the answer is not a release")
    tag = str(raw.get("tag_name") or "")
    version = _version_of_tag(tag)
    html_url = str(raw.get("html_url") or f"{RELEASES_PAGE}/tag/{tag}")
    notes = str(raw.get("body") or "").strip()[:NOTES_LIMIT]
    published = raw.get("published_at")
    assets = raw.get("assets") if isinstance(raw.get("assets"), list) else []
    wanted = installer_name_for(version)
    chosen: dict[str, Any] | None = None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == wanted:
            chosen = asset
            break
    if chosen is None:
        for asset in assets:
            if isinstance(asset, dict) and INSTALLER_RE.match(str(asset.get("name") or "")):
                chosen = asset
                break
    name = url = sha256 = ""
    size = 0
    if chosen is not None:
        candidate_url = str(chosen.get("browser_download_url") or "")
        if candidate_url.startswith(DOWNLOAD_PREFIX):
            name = str(chosen.get("name"))
            url = candidate_url
            try:
                size = max(0, int(chosen.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            sha256 = _sha256_of_digest(chosen.get("digest"))
        else:
            log.warning(
                "Ignoring an installer asset outside %s: %s", DOWNLOAD_PREFIX, candidate_url
            )
    return Release(
        version=version,
        tag=tag,
        html_url=html_url,
        notes=notes,
        published_at=str(published) if published else None,
        installer_name=name,
        installer_url=url,
        installer_size=size,
        installer_sha256=sha256,
    )


def release_from_redirect(location: str) -> Release | None:
    """The release named by the redirect of ``/releases/latest``; ``None`` when GitHub sent
    the release list instead (nothing is published yet)."""
    path = urlsplit(location).path.rstrip("/")
    marker = "/releases/tag/"
    if marker not in path:
        if path.endswith("/releases"):
            return None
        raise ValueError(f"unexpected address {location!r}")
    tag = unquote(path.split(marker, 1)[1])
    version = _version_of_tag(tag)
    name = installer_name_for(version)
    return Release(
        version=version,
        tag=tag,
        html_url=f"{RELEASES_PAGE}/tag/{tag}",
        installer_name=name,
        installer_url=f"{DOWNLOAD_PREFIX}{tag}/{name}",
    )


def http_client() -> httpx.Client:
    """A client that trusts the Windows certificate store (antivirus HTTPS scanning and
    company proxies add their own roots there) and the system proxy settings."""
    return httpx.Client(
        verify=ssl.create_default_context(),
        headers={"User-Agent": f"9SigmaEditor/{__version__} (update check)"},
        follow_redirects=False,
        trust_env=True,
    )


CHECK_OFFLINE = (
    "this computer could not reach GitHub. Check the internet connection; the app tries again later"
)
CHECK_REFUSED = "GitHub is not answering right now. The app tries again later"
CHECK_MISSING = (
    "the release page on GitHub was not found. Ask whoever publishes the updates whether the "
    "project is still public"
)


def fetch_latest_release(client: httpx.Client) -> Release | None:
    """The newest published release, or ``None`` when nothing is published yet.

    The API is asked first; its answer carries the installer's size and digest. When it does
    not give one (rate limit, a hiccup), the release page's redirect is read instead.
    Raises :class:`UpdateCheckError` with a plain-English reason when neither answers.
    """
    api_missing = False
    offline = False
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        response = client.get(API_LATEST, headers=headers, timeout=CHECK_TIMEOUT_S)
        if response.status_code == 200:
            try:
                return release_from_api(response.json())
            except ValueError as exc:
                log.warning("Update check: the GitHub API answer was not usable: %s", exc)
        elif response.status_code == 404:
            api_missing = True
        else:
            log.info("Update check: the GitHub API answered %s", response.status_code)
    except httpx.HTTPError as exc:
        offline = isinstance(exc, httpx.TransportError)
        log.info("Update check: the GitHub API did not answer: %s", exc)

    try:
        response = client.get(WEB_LATEST, timeout=CHECK_TIMEOUT_S)
    except httpx.HTTPError as exc:
        log.info("Update check: the release page did not answer: %s", exc)
        raise UpdateCheckError(
            CHECK_OFFLINE if offline or isinstance(exc, httpx.TransportError) else CHECK_REFUSED
        ) from exc
    if response.is_redirect:
        location = response.headers.get("location", "")
        try:
            return release_from_redirect(location)
        except ValueError as exc:
            log.warning("Update check: %s", exc)
            raise UpdateCheckError(CHECK_REFUSED) from exc
    if response.status_code == 404 or api_missing:
        raise UpdateCheckError(CHECK_MISSING)
    raise UpdateCheckError(CHECK_REFUSED)


# The installed copy ---------------------------------------------------------------------------


def install_root(module_file: str | os.PathLike[str] | None = None) -> Path | None:
    """The folder of an installed copy (``runtime\\`` and ``app\\`` side by side, with
    ``install-info.json``), or ``None`` for a developer's checkout."""
    here = Path(module_file or __file__).resolve()
    try:
        app_dir, root = here.parents[2], here.parents[3]
    except IndexError:
        return None
    if app_dir.name.lower() != "app":
        return None
    if not (root / INSTALL_INFO).is_file() or not (root / "runtime" / "python.exe").is_file():
        return None
    return root


def default_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / DOWNLOAD_DIR_NAME


class _Slice:
    """Just enough of a file object over one region of a file for :mod:`zipfile`, so the
    payload appended to the installer is read where it is."""

    def __init__(self, path: Path, offset: int, size: int) -> None:
        self._handle = open(path, "rb")  # noqa: SIM115 - closed by close()
        self._offset, self._size, self._pos = offset, size, 0

    def read(self, n: int = -1) -> bytes:
        remaining = self._size - self._pos
        n = remaining if n is None or n < 0 or n > remaining else n
        self._handle.seek(self._offset + self._pos)
        data = self._handle.read(n)
        self._pos += len(data)
        return data

    def seek(self, pos: int, whence: int = 0) -> int:
        if whence == 1:
            pos += self._pos
        elif whence == 2:
            pos += self._size
        self._pos = max(0, min(self._size, pos))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self._handle.close()


def embedded_version(path: Path) -> str | None:
    """The version in the installer's appended payload (``install-info.json``), or ``None``
    when the file is not a 9Sigma Editor installer."""
    try:
        total = path.stat().st_size
        if total <= TRAILER.size:
            return None
        with open(path, "rb") as handle:
            handle.seek(total - TRAILER.size)
            offset, magic = TRAILER.unpack(handle.read(TRAILER.size))
        if magic != PAYLOAD_MAGIC or not 0 < offset < total - TRAILER.size:
            return None
        part = _Slice(path, offset, total - TRAILER.size - offset)
        try:
            with zipfile.ZipFile(part) as archive:  # type: ignore[arg-type]
                info = json.loads(archive.read(PAYLOAD_INFO).decode("utf-8"))
        finally:
            part.close()
        version = info.get("version") if isinstance(info, dict) else None
        return str(version) if version else None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, struct.error):
        return None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_installer(path: Path, release: Release) -> str:
    """``""`` when ``path`` is exactly the installer the release describes, else what is wrong
    (plain English)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(2)
    except OSError as exc:
        return f"the downloaded file cannot be read ({exc})"
    if release.installer_size and size != release.installer_size:
        return (
            f"the downloaded file is {size:,} bytes but the release says {release.installer_size:,}"
        )
    if release.installer_sha256 and sha256_of(path) != release.installer_sha256:
        return "the downloaded file does not match the checksum GitHub published for it"
    if head != b"MZ":
        return "the downloaded file is not a Windows program"
    inside = embedded_version(path)
    if inside is None:
        return "the downloaded file is not a 9Sigma Editor installer"
    if compare_versions(inside, release.version) != 0:
        return (
            f"the downloaded installer contains version {inside}, not {release.version}. Tell "
            "whoever publishes the updates"
        )
    return ""


def launch_installer(installer: Path, target: Path) -> None:
    """Start the installer detached from this process, in its no-click mode: it waits for this
    process to end, installs into ``target`` and opens the app again."""
    args = [
        str(installer),
        *INSTALLER_ARGS,
        "--wait-pid",
        str(os.getpid()),
        "--target",
        str(target),
    ]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    subprocess.Popen(  # noqa: S603 - a verified installer from the release, fixed arguments
        args, close_fds=True, creationflags=flags, cwd=str(installer.parent)
    )
    log.info("Started the installer %s", " ".join(args))


def request_app_exit(delay: float = EXIT_DELAY_S, force_after: float = FORCE_EXIT_AFTER_S) -> None:
    """Close the app a moment after this request has been answered: the app window first
    (``sge open`` then returns and the process ends), then the process itself if it is still
    there (``sge serve``, a browser window, or work that does not stop)."""

    def run() -> None:
        time.sleep(delay)
        try:
            import webview

            for window in list(getattr(webview, "windows", []) or []):
                window.destroy()
        except Exception as exc:  # noqa: BLE001 - the forced exit below still happens
            log.warning("Could not close the app window for the update: %s", exc)
        time.sleep(force_after)
        log.info("Ending the app for the update")
        os._exit(0)

    threading.Thread(target=run, name="sge-update-exit", daemon=True).start()


def _thread_runner(name: str, work: Callable[[], None]) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()


# State ----------------------------------------------------------------------------------------


class UpdateInstaller(BaseModel):
    name: str
    size: int
    url: str


class UpdateProgress(BaseModel):
    done: int
    total: int


class UpdateStatus(BaseModel):
    """``GET /api/system/update`` (docs/RELEASE-CONTRACT.md section 5)."""

    current_version: str
    status: UpdateState
    checking: bool = False
    message: str = ""
    latest_version: str | None = None
    release_url: str | None = None
    release_notes: str = ""
    published_at: str | None = None
    installer: UpdateInstaller | None = None
    can_install: bool = False
    install_blocker: str = ""
    checked_at: str | None = None
    progress: UpdateProgress | None = None


def _iso(moment: float | None) -> str | None:
    if moment is None:
        return None
    return datetime.fromtimestamp(moment, UTC).isoformat(timespec="seconds")


def _megabytes(count: int) -> str:
    return f"{count / 1048576:.0f}"


class UpdateManager:
    """The update state of one running app, safe to use from any thread.

    Everything slow happens in ``runner`` (a daemon thread by default; tests pass one that
    runs the work at once) and ``client_factory`` makes the HTTP client, so tests never touch
    the network.
    """

    def __init__(
        self,
        *,
        current_version: str = __version__,
        root: Path | None | Literal["auto"] = "auto",
        client_factory: Callable[[], httpx.Client] = http_client,
        runner: Callable[[str, Callable[[], None]], None] = _thread_runner,
        launcher: Callable[[Path, Path], None] = launch_installer,
        exiter: Callable[[], None] = request_app_exit,
        busy: Callable[[], str] = lambda: "",
        download_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
        windows: bool | None = None,
    ) -> None:
        self.current_version = current_version
        self.root = install_root() if root == "auto" else root
        self.client_factory = client_factory
        self.runner = runner
        self.launcher = launcher
        self.exiter = exiter
        self.busy = busy
        self.download_dir = download_dir or default_download_dir()
        self.clock = clock
        self.windows = sys.platform == "win32" if windows is None else windows
        self._lock = threading.Lock()
        self._state: UpdateState = "unknown"
        self._checking = False
        self._release: Release | None = None
        self._no_release = False
        self._check_error = ""
        self._install_error = ""
        self._checked_at: float | None = None
        self._failed_at: float | None = None
        self._progress: tuple[int, int] | None = None

    # Reading ------------------------------------------------------------------------------

    def _blocker_locked(self) -> str:
        if not self.windows:
            return NOT_WINDOWS_BLOCKER
        if self.root is None:
            return NOT_INSTALLED_BLOCKER
        if self._release is None or not self._release.installer_url:
            return NO_INSTALLER_BLOCKER
        if self._state in ("downloading", "installing"):
            return "The update is already being installed."
        return ""

    def status(self) -> UpdateStatus:
        busy = ""
        try:
            busy = self.busy() or ""
        except Exception as exc:  # noqa: BLE001 - a status read must never fail
            log.warning("Could not tell whether the app is busy: %s", exc)
        with self._lock:
            release = self._release
            blocker = self._blocker_locked()
            offerable = release is not None and is_newer(release.version, self.current_version)
            if not offerable:
                blocker = blocker or "There is no newer version to install."
            if not blocker and busy:
                blocker = busy
            installer = None
            if release is not None and release.installer_url:
                installer = UpdateInstaller(
                    name=release.installer_name,
                    size=release.installer_size,
                    url=release.installer_url,
                )
            progress = None
            if self._state == "downloading" and self._progress is not None:
                progress = UpdateProgress(done=self._progress[0], total=self._progress[1])
            return UpdateStatus(
                current_version=self.current_version,
                status=self._state,
                checking=self._checking,
                message=self._message_locked(),
                latest_version=release.version if release else None,
                release_url=(release.html_url if release else RELEASES_PAGE),
                release_notes=release.notes if release else "",
                published_at=release.published_at if release else None,
                installer=installer,
                can_install=offerable and not blocker,
                install_blocker=blocker if offerable else "",
                checked_at=_iso(self._checked_at),
                progress=progress,
            )

    def _message_locked(self) -> str:
        release = self._release
        if self._state == "unknown":
            return "Checking for updates." if self._checking else ""
        if self._state == "up_to_date":
            if self._no_release:
                return "No update has been published yet."
            return f"9Sigma Editor is up to date (version {self.current_version})."
        if self._state == "available" and release is not None:
            return (
                f"Version {release.version} is available. This laptop has {self.current_version}."
            )
        if self._state == "check_failed":
            return f"Could not check for updates: {self._check_error}."
        if self._state == "downloading":
            if self._progress and self._progress[1]:
                done, total = self._progress
                return f"Downloading the update ({_megabytes(done)} of {_megabytes(total)} MB)."
            return "Downloading the update."
        if self._state == "installing":
            return (
                "Installing the update. 9Sigma Editor closes now and opens again by itself in "
                "about a minute."
            )
        if self._state == "install_failed":
            return f"The update could not be installed: {self._install_error}."
        return ""

    # Checking -----------------------------------------------------------------------------

    def _check_due_locked(self, now: float) -> bool:
        if self._checking or self._state in ("downloading", "installing"):
            return False
        if self._checked_at is None and self._failed_at is None:
            return True
        if self._state == "check_failed" and self._failed_at is not None:
            return now - self._failed_at >= RETRY_AFTER_FAILURE_S
        last = max(self._checked_at or 0.0, self._failed_at or 0.0)
        return now - last >= CHECK_EVERY_S

    def maybe_check(self) -> bool:
        """Start a background check when one is due. Returns True when one was started."""
        with self._lock:
            if not self._check_due_locked(self.clock()):
                return False
            self._checking = True
        self.runner("sge-update-check", self._run_check)
        return True

    def check_now(self) -> bool:
        """Start a check unless one (or an install) is already running."""
        with self._lock:
            if self._checking or self._state in ("downloading", "installing"):
                return False
            self._checking = True
        self.runner("sge-update-check", self._run_check)
        return True

    def _run_check(self) -> None:
        try:
            with self.client_factory() as client:
                release = fetch_latest_release(client)
        except UpdateCheckError as exc:
            self._finish_check(error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a failed check must never take the app down
            log.exception("Update check failed")
            self._finish_check(error=f"something unexpected happened ({type(exc).__name__})")
            return
        self._finish_check(release=release)

    def _finish_check(self, release: Release | None = None, error: str = "") -> None:
        now = self.clock()
        with self._lock:
            self._checking = False
            if self._state in ("downloading", "installing"):
                return
            if error:
                self._failed_at = now
                self._check_error = error
                if self._state == "available":
                    return  # keep offering the version found earlier
                self._state = "check_failed"
                return
            self._checked_at = now
            self._failed_at = None
            self._check_error = ""
            self._release = release
            self._no_release = release is None
            if release is not None and is_newer(release.version, self.current_version):
                if self._state != "install_failed" or self._install_error == "":
                    self._state = "available"
                log.info(
                    "Update check: running %s, %s is available",
                    self.current_version,
                    release.version,
                )
            else:
                self._state = "up_to_date"

    # Installing ---------------------------------------------------------------------------

    def start_install(self) -> UpdateStatus:
        """Download, check and start the installer in the background.

        Raises :class:`UpdateInstallError` (shown as a 409) when it cannot start now.
        """
        busy = self.busy() or ""
        with self._lock:
            release = self._release
            if self._state in ("downloading", "installing"):
                raise UpdateInstallError("The update is already being installed.")
            if release is None or not is_newer(release.version, self.current_version):
                raise UpdateInstallError(
                    "There is no newer version to install. Check for updates first."
                )
            blocker = self._blocker_locked() or busy
            if blocker:
                raise UpdateInstallError(blocker)
            self._state = "downloading"
            self._install_error = ""
            self._progress = (0, release.installer_size)
        self.runner("sge-update-install", lambda: self._run_install(release))
        return self.status()

    def _set_progress(self, done: int, total: int) -> None:
        with self._lock:
            if self._state == "downloading":
                self._progress = (done, total)

    def _fail_install(self, reason: str) -> None:
        with self._lock:
            self._state = "install_failed"
            self._install_error = reason.rstrip(".")
            self._progress = None

    def _run_install(self, release: Release) -> None:
        assert self.root is not None
        try:
            installer = self.download(release)
        except UpdateInstallError as exc:
            log.warning("Update download failed: %s", exc)
            self._fail_install(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Update download failed")
            self._fail_install(f"something unexpected happened ({type(exc).__name__})")
            return
        try:
            self.launcher(installer, self.root)
        except OSError as exc:
            log.warning("Could not start the installer: %s", exc)
            self._fail_install(f"the installer could not be started ({exc})")
            return
        with self._lock:
            self._state = "installing"
            self._progress = None
        self.exiter()

    def download(self, release: Release) -> Path:
        """The verified installer in the download folder (reused when already there)."""
        folder = self.download_dir
        name = release.installer_name or installer_name_for(release.version)
        target = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UpdateInstallError(f"the temporary folder cannot be used ({exc})") from exc
        reusable = target.is_file() and release.installer_sha256
        if reusable and verify_installer(target, release) == "":
            size = target.stat().st_size
            self._set_progress(size, size)
            log.info("Update %s: the verified installer is already at %s", release.version, target)
            return target
        partial = target.with_name(target.name + ".part")
        done = 0
        total = release.installer_size
        try:
            with (
                self.client_factory() as client,
                client.stream(
                    "GET", release.installer_url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT
                ) as response,
            ):
                if response.url.scheme != "https":
                    raise UpdateInstallError(
                        "the download was sent to an address that is not secure"
                    )
                if response.status_code == 404:
                    raise UpdateInstallError("the installer is no longer on the release page")
                response.raise_for_status()
                if "text/html" in response.headers.get("content-type", ""):
                    raise UpdateInstallError("GitHub sent a web page instead of the installer")
                total = int(response.headers.get("content-length") or 0) or total
                if total > MAX_INSTALLER_BYTES:
                    raise UpdateInstallError("the file on the release page is far too big")
                self._set_progress(0, total)
                with open(partial, "wb") as handle:
                    for chunk in response.iter_bytes(256 * 1024):
                        handle.write(chunk)
                        done += len(chunk)
                        if done > MAX_INSTALLER_BYTES:
                            raise UpdateInstallError("the file on the release page is far too big")
                        self._set_progress(done, total)
        except UpdateInstallError:
            _remove_quietly(partial)
            raise
        except httpx.HTTPStatusError as exc:
            _remove_quietly(partial)
            raise UpdateInstallError(
                f"GitHub answered {exc.response.status_code} for the installer. Try again later"
            ) from exc
        except httpx.HTTPError as exc:
            _remove_quietly(partial)
            raise UpdateInstallError(
                "the download did not finish. Check the internet connection and try again"
            ) from exc
        except OSError as exc:
            _remove_quietly(partial)
            raise UpdateInstallError(
                f"the update could not be saved in the temporary folder ({exc})"
            ) from exc
        if total and done != total:
            _remove_quietly(partial)
            raise UpdateInstallError(
                f"the download stopped early ({_megabytes(done)} of {_megabytes(total)} MB)"
            )
        problem = verify_installer(partial, release)
        if problem:
            _remove_quietly(partial)
            raise UpdateInstallError(problem)
        try:
            os.replace(partial, target)
        except OSError as exc:
            _remove_quietly(partial)
            raise UpdateInstallError(f"the installer could not be saved ({exc})") from exc
        log.info("Downloaded and verified the update %s: %s", release.version, target)
        return target


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
