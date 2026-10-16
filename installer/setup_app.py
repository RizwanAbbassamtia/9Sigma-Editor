"""The 9Sigma Editor installer: a window that opens at once and owns every step from
"checking this computer" to "the app is open" (docs/RELEASE-CONTRACT.md section 3).

    Install-9SigmaEditor-<version>.exe                  the person double-clicks this
    Install-9SigmaEditor-<version>.exe --auto --open    what Update now runs: no clicks, the
                                                         app opens again when it is done
    Install-9SigmaEditor-<version>.exe --unattended     no window; exit code 0 = installed
    Install-9SigmaEditor-<version>.exe --target D:\\x   install somewhere else (testing)
    ... --skip-ffmpeg                                    do not download FFmpeg (testing, offline)
    uninstall.exe --uninstall                            Settings > Apps > 9Sigma Editor
    python installer\\setup_app.py --payload build\\9SigmaEditor-<version>-payload.zip

The payload -- the whole app with its own Python (dist/9SigmaEditor from
tools/build_runtime.py) -- is a zip appended to this program by tools/make_setup_exe.py, with a
16-byte trailer saying where it starts. No administrator rights are needed: the install is a
copy into the person's own %LOCALAPPDATA%\\Programs, then the one helper program the app needs
(FFmpeg from gyan.dev, which makes the videos), then a self-test.

Modelled on the owner's earlier installer (SceneForge), whose design rules each answer a way an
installer failed before:
  - the window is on screen before any work starts
  - every step names itself, and the progress bar only moves for real work
  - a step that produces nothing for a while is called out, with what to do
  - nothing fails silently: an error says what happened and what to do next, with Retry,
    View log, Copy error details and Close
  - the log is written from the first line, at a path the person can find

Standard library only (tkinter for the window): it is frozen with PyInstaller.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "9Sigma Editor"
FOLDER = "9SigmaEditor"
MAGIC = b"SIGMA9ED"
TRAILER = struct.Struct("<Q8s")  # payload offset, magic
INFO_NAME = "install-info.json"
MUTEX_NAME = "Local\\9SigmaEditor-Setup"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\9SigmaEditor"
RELEASES_PAGE = "https://github.com/RizwanAbbassamtia/9Sigma-Editor/releases"
SHORTCUT_NAME = APP_NAME + ".lnk"
UNINSTALLER = "uninstall.exe"
LAUNCH_ARGS = ["-m", "sigma_editor.launch"]
ICON_IN_APP = Path("app") / "assets" / "icons" / "sigma.ico"

PROGRAM_DIRS = ("runtime", "app")
PROGRAM_FILES = ("VERSION", INFO_NAME, "9SigmaEditor.cmd", "9SigmaEditor-Server.cmd")
NEEDED_MARGIN_MB = 300
MAX_PATH = 259
APP_CLOSE_WAIT_S = 90
PID_WAIT_S = 120
SELF_TEST_TIMEOUT_S = 420
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
DETACHED = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
"""DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the started program outlives this one."""

LOG_PATH = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "9SigmaEditor-install.log"
_instance_mutex: int | None = None


def default_target() -> Path:
    override = os.environ.get("SGE_INSTALL_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(override))
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Programs" / FOLDER


def staging_for(target: Path) -> Path:
    return target.with_name(target.name + ".new")


# Logging --------------------------------------------------------------------------------------


def log(text: str) -> None:
    line = "[{}] {}".format(datetime.now().strftime("%H:%M:%S"), text)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def start_log(target: Path, argv: list[str]) -> None:
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as handle:
            handle.write(f"{APP_NAME} setup  {datetime.now()}\n")
            handle.write(
                "user: {}   machine: {}   windows: {}\n".format(
                    os.environ.get("USERNAME", "?"), platform.node(), platform.platform()
                )
            )
            handle.write(f"target: {target}\nargs: {' '.join(argv)}\n")
    except OSError:
        pass


# One at a time --------------------------------------------------------------------------------


def claim_instance() -> bool:
    """True when this is the only installer running. When the name cannot be made at all
    the install goes ahead rather than being refused on a guess."""
    global _instance_mutex
    if sys.platform != "win32" or _instance_mutex:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _instance_mutex = handle
        return True
    except Exception:  # noqa: BLE001 - never block an install on this
        return True


def release_instance() -> None:
    global _instance_mutex
    if _instance_mutex:
        try:
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(_instance_mutex))
        except Exception:  # noqa: BLE001
            pass
        _instance_mutex = None


# Errors ---------------------------------------------------------------------------------------


class InstallError(Exception):
    """Something the person needs to read: ``what`` happened in plain words, the single
    action to take (``fix``), and ``detail`` for the log and the Copy button."""

    def __init__(self, what: str, fix: str, detail: str = "") -> None:
        super().__init__(what)
        self.what = what
        self.fix = fix
        self.detail = detail


class ToolError(Exception):
    """A helper program (FFmpeg) could not be downloaded or started (plain English)."""


# The payload ----------------------------------------------------------------------------------


class _Slice:
    """A file object over one region of a bigger file, so zipfile can read the payload
    appended to this program without copying it out."""

    def __init__(self, path: Path, offset: int, size: int) -> None:
        self._handle = open(path, "rb")  # noqa: SIM115 - closed by close()
        self._offset, self._size, self._pos = offset, size, 0

    def read(self, n: int = -1) -> bytes:
        remaining = self._size - self._pos
        if n is None or n < 0 or n > remaining:
            n = remaining
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


@dataclass
class Payload:
    describe: str
    opener: Callable[[], Any]
    size: int
    offset: int = 0
    """Where the payload starts inside this program (0 for a separate zip): the bytes before
    it are the setup program alone, which becomes uninstall.exe."""

    def open_zip(self) -> zipfile.ZipFile:
        return zipfile.ZipFile(self.opener())


def read_trailer(path: Path) -> tuple[int, int] | None:
    """(offset, size) of a payload appended to ``path``, or None."""
    try:
        total = path.stat().st_size
        if total <= TRAILER.size:
            return None
        with open(path, "rb") as handle:
            handle.seek(total - TRAILER.size)
            offset, magic = TRAILER.unpack(handle.read(TRAILER.size))
    except (OSError, struct.error):
        return None
    if magic != MAGIC or not 0 < offset < total - TRAILER.size:
        return None
    return offset, total - TRAILER.size - offset


def find_payload(explicit: str | None = None, program: Path | None = None) -> Payload | None:
    """The zip appended to this program; or one named on the command line."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return Payload(str(path), lambda: open(path, "rb"), path.stat().st_size)  # noqa: SIM115
        return None
    exe = program or (Path(sys.executable) if getattr(sys, "frozen", False) else None)
    if exe is not None:
        found = read_trailer(exe)
        if found is not None:
            offset, size = found
            return Payload(f"inside {exe.name}", lambda: _Slice(exe, offset, size), size, offset)
    return None


# Requirements ---------------------------------------------------------------------------------


@dataclass
class Requirement:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    fix: str = ""


@dataclass
class PayloadInfo:
    version: str = ""
    files: int = 0
    unpacked: int = 0
    longest: int = 0


def inspect_payload(payload: Payload) -> PayloadInfo:
    prefix = FOLDER + "/"
    with payload.open_zip() as archive:
        info = json.loads(archive.read(prefix + INFO_NAME).decode("utf-8"))
        entries = [e for e in archive.infolist() if e.filename.startswith(prefix)]
    return PayloadInfo(
        version=str(info.get("version", "")),
        files=len(entries),
        unpacked=sum(e.file_size for e in entries),
        longest=max((len(e.filename) - len(prefix) for e in entries), default=0),
    )


def free_mb(path: Path) -> float:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free / 1048576
    except OSError:
        return -1.0


def webview2_version() -> str:
    if sys.platform != "win32":
        return "not needed"
    import winreg

    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, sub in (
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + client),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + client),
        (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + client),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                value = str(winreg.QueryValueEx(key, "pv")[0]).strip()
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return value
    return ""


def installed_version(target: Path) -> str:
    try:
        raw = json.loads((target / INFO_NAME).read_text(encoding="utf-8"))
        return str(raw.get("version") or "?")
    except (OSError, ValueError):
        return "?" if (target / "runtime" / "python.exe").exists() else ""


def check_requirements(
    payload: Payload | None, target: Path, skip_ffmpeg: bool = False
) -> tuple[list[Requirement], PayloadInfo]:
    out: list[Requirement] = []
    info = PayloadInfo()

    log("requirements: Windows")
    release = platform.release()
    build = 0
    try:
        build = int(platform.version().split(".")[2])
    except (IndexError, ValueError):
        pass
    is_64 = platform.machine().lower() in ("amd64", "x86_64")
    if sys.platform == "win32" and is_64 and (release in ("10", "11") or build >= 10240):
        out.append(Requirement("Windows", "ok", f"Windows {release} 64-bit (build {build})"))
    else:
        out.append(
            Requirement(
                "Windows",
                "fail",
                f"{platform.system()} {release}",
                f"{APP_NAME} needs 64-bit Windows 10 or 11.",
            )
        )

    log("requirements: installer files")
    if payload is None:
        out.append(
            Requirement(
                "Installer files",
                "fail",
                "The program files are missing from this installer.",
                "Download the installer again and run it. If it happens twice, the download "
                "is being cut short.",
            )
        )
    else:
        try:
            info = inspect_payload(payload)
            out.append(
                Requirement(
                    "Installer files",
                    "ok",
                    f"{APP_NAME} {info.version}, {info.files} files "
                    f"({info.unpacked / 1048576:.0f} MB)",
                )
            )
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            out.append(
                Requirement(
                    "Installer files",
                    "fail",
                    "The installer is damaged.",
                    f"Download it again and run it. Detail: {exc}",
                )
            )

    log("requirements: disk space")
    needed = info.unpacked / 1048576 * 2 + sum(t.disk_mb for t in TOOLS) + NEEDED_MARGIN_MB
    free = free_mb(target)
    if free < 0:
        out.append(Requirement("Disk space", "warn", "Could not measure the free space."))
    elif free >= needed:
        out.append(
            Requirement(
                "Disk space", "ok", f"{free / 1024:.1f} GB free, {needed / 1024:.1f} GB needed"
            )
        )
    else:
        out.append(
            Requirement(
                "Disk space",
                "fail",
                f"{free:.0f} MB free, {needed:.0f} MB needed",
                f"Free up space on drive {target.drive or 'C:'} and click Check again.",
            )
        )

    log("requirements: install folder")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        probe = target.parent / f".sigma-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        out.append(Requirement("Install folder", "ok", str(target)))
    except OSError as exc:
        out.append(
            Requirement(
                "Install folder",
                "fail",
                f"Cannot write to {target.parent}",
                "Your Windows account cannot write to its own AppData folder. Ask whoever "
                f"manages this laptop. Detail: {exc}",
            )
        )

    longest = len(str(staging_for(target))) + 1 + info.longest
    if info.longest and longest > MAX_PATH:
        out.append(
            Requirement(
                "Folder name length",
                "fail",
                f"The install folder's path is too long for Windows ({longest} characters).",
                "Install into a folder with a shorter path, for example: "
                f"Install-9SigmaEditor.exe --target C:\\{FOLDER}",
            )
        )

    out.append(Requirement("Python", "ok", "3.12, included: nothing else to install"))
    for tool, skipped in ((FFMPEG, skip_ffmpeg),):
        if skipped:
            out.append(Requirement(tool.name, "ok", f"not downloaded (--skip-{tool.key})"))
            continue
        current = tool_version(tool, tool.folder_in(target) / tool.programs[0])
        if current:
            out.append(Requirement(tool.name, "ok", f"already installed: {current[:60]}"))
        else:
            out.append(Requirement(
                tool.name, "ok",
                f"will be downloaded from {tool.source} (about {tool.approx_mb} MB)"))
    edge = webview2_version()
    if edge:
        out.append(Requirement("WebView2", "ok", f"Microsoft Edge WebView2 {edge}"))
    else:
        out.append(
            Requirement(
                "WebView2",
                "warn",
                "Not found: the app will open in the web browser instead of its own window.",
                "Install the Evergreen WebView2 runtime from "
                "https://developer.microsoft.com/microsoft-edge/webview2/ (optional).",
            )
        )
    old = installed_version(target)
    if old:
        out.append(
            Requirement(
                "Existing install",
                "ok",
                f"version {old} found; it will be updated. Your projects and settings are kept.",
            )
        )
    return out, info


# Shortcuts and the uninstall entry ------------------------------------------------------------


def system_program(*parts: str) -> str:
    """A Windows program by its full path (an update started by the app may run with a PATH
    that holds little more than System32)."""
    path = Path(os.environ.get("SystemRoot") or r"C:\Windows").joinpath("System32", *parts)
    return str(path) if path.exists() else parts[-1]


def _powershell(script: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [system_program("WindowsPowerShell", "v1.0", "powershell.exe"), "-NoProfile",
         "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
        check=False,
    )


def _ps(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


SHORTCUT_FOLDERS = (
    "[Environment]::GetFolderPath('Desktop')",
    "[Environment]::GetFolderPath('Programs')",
)


def create_shortcuts(target: Path) -> None:
    pythonw = target / "runtime" / "pythonw.exe"
    icon = target / ICON_IN_APP
    script = (
        "$ErrorActionPreference = 'Stop'; $s = New-Object -ComObject WScript.Shell; "
        f"foreach ($dir in @({', '.join(SHORTCUT_FOLDERS)})) {{ "
        "if (-not $dir) { continue }; "
        f"$l = $s.CreateShortcut((Join-Path $dir {_ps(SHORTCUT_NAME)})); "
        f"$l.TargetPath = {_ps(pythonw)}; $l.Arguments = {_ps(' '.join(LAUNCH_ARGS))}; "
        f"$l.WorkingDirectory = {_ps(target)}; $l.IconLocation = {_ps(str(icon) + ',0')}; "
        f"$l.Description = {_ps(APP_NAME)}; $l.Save() }}"
    )
    result = _powershell(script)
    if result.returncode != 0:
        raise OSError((result.stdout + result.stderr).strip()[:600] or "PowerShell failed")


def remove_shortcuts(target: Path) -> list[str]:
    """Delete the shortcuts that start the copy in ``target`` (never another copy's)."""
    script = (
        "$s = New-Object -ComObject WScript.Shell; $gone = @(); "
        f"foreach ($dir in @({', '.join(SHORTCUT_FOLDERS)})) {{ "
        "if (-not $dir) { continue }; "
        f"$p = Join-Path $dir {_ps(SHORTCUT_NAME)}; "
        "if (Test-Path -LiteralPath $p) { $t = $s.CreateShortcut($p).TargetPath; "
        f"if ($t -and $t.StartsWith({_ps(str(target) + os.sep)}, "
        "[StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $p -Force; "
        "$gone += $p } } }; $gone -join [Environment]::NewLine"
    )
    try:
        result = _powershell(script)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"could not remove the shortcuts: {exc}")
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def registered_location() -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(str(winreg.QueryValueEx(key, "InstallLocation")[0]))
    except OSError:
        return None


def same_folder(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def write_uninstall_entry(target: Path, version: str, uninstall_command: str) -> None:
    import winreg

    size_kb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) // 1024
    values: dict[str, Any] = {
        "DisplayName": APP_NAME,
        "DisplayVersion": version,
        "Publisher": APP_NAME,
        "InstallLocation": str(target),
        "DisplayIcon": str(target / ICON_IN_APP),
        "UninstallString": uninstall_command,
        "QuietUninstallString": uninstall_command + " --unattended",
        "URLInfoAbout": RELEASES_PAGE,
        "InstallDate": datetime.now().strftime("%Y%m%d"),
    }
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, number in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(min(number, 0xFFFFFFFF)))


def remove_uninstall_entry(target: Path) -> bool:
    """Remove the entry when it is this copy's (a test copy never removes the real one's)."""
    if sys.platform != "win32" or not same_folder(registered_location(), target):
        return False
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        return True
    except OSError as exc:
        log(f"could not remove the uninstall entry: {exc}")
        return False


def write_uninstaller(payload: Payload | None, target: Path) -> str:
    """Put the setup program, without its payload, in the install folder as uninstall.exe and
    return the command Windows runs to uninstall."""
    if getattr(sys, "frozen", False) and payload is not None and payload.offset:
        destination = target / UNINSTALLER
        with open(sys.executable, "rb") as source, open(destination, "wb") as out:
            remaining = payload.offset
            while remaining:
                chunk = source.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise OSError("the setup program ended early")
                out.write(chunk)
                remaining -= len(chunk)
        return f'"{destination}" --uninstall'
    script = Path(__file__).resolve()
    return f'"{sys.executable}" "{script}" --uninstall --target "{target}"'


# Helper program: FFmpeg ----------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """A program the app needs that the installer downloads into ``runtime\\`` rather than
    ships (too big, or updated too often): where it comes from, which files are kept, how to
    ask its version, and what the app cannot do without it."""

    key: str
    name: str
    title: str
    """The step's title in the setup window."""
    url: str
    checksum_suffix: str
    """The SHA-256 published beside the zip is at ``url + checksum_suffix``."""
    folder: tuple[str, ...]
    """Where the programs go, inside ``runtime\\``; the first part is kept across updates."""
    programs: tuple[str, ...]
    """The files kept from the zip; the first one is started to check that it works."""
    version_args: tuple[str, ...]
    source: str
    approx_mb: int
    disk_mb: int
    needed_for: str

    @property
    def command(self) -> str:
        return Path(self.programs[0]).stem

    def folder_in(self, target: Path) -> Path:
        return target.joinpath("runtime", *self.folder)


FFMPEG = Tool(
    key="ffmpeg",
    name="FFmpeg",
    title="Getting FFmpeg, the video tool",
    url=os.environ.get(
        "SGE_FFMPEG_URL", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    ),
    checksum_suffix=".sha256",
    folder=("ffmpeg", "bin"),
    programs=("ffmpeg.exe", "ffprobe.exe"),
    version_args=("-hide_banner", "-version"),
    source="gyan.dev",
    approx_mb=110,
    disk_mb=400,
    needed_for="to make videos",
)
TOOLS = (FFMPEG,)
"""Every program the installer downloads (one today): the disk space check, the requirements
table and the carry-over on an update all read this."""


def tool_version(tool: Tool, exe: Path | str) -> str:
    """The first line of the program's version output, or "" when it does not run."""
    try:
        result = subprocess.run(
            [str(exe), *tool.version_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = (result.stdout or "").strip().splitlines()
    return lines[0].strip() if result.returncode == 0 and lines else ""


def _open_url(url: str, timeout: float = 60.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "9SigmaEditor-Setup"})
    if url.startswith("https:"):
        context = ssl.create_default_context()  # the Windows certificate store
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - file: in tests


def _plain(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"the download site answered {exc.code}"
    if isinstance(exc, urllib.error.URLError | TimeoutError | ConnectionError):
        return "this computer could not reach the download site (check the internet connection)"
    return str(exc) or type(exc).__name__


_SHA256 = re.compile(r"\b[0-9A-Fa-f]{64}\b")


def published_sha256(url: str) -> str:
    """The SHA-256 in the checksum file at ``url`` ("<hash>  <file>", gyan.dev's layout; any
    text holding one hash does), or "" when it cannot be read."""
    try:
        with _open_url(url, timeout=20) as response:
            text = response.read(8192).decode("ascii", "replace")
    except Exception as exc:  # noqa: BLE001 - the checksum is a bonus, not a requirement
        log(f"no published checksum at {url}: {exc}")
        return ""
    match = _SHA256.search(text)
    return match.group(0).lower() if match else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, progress: Callable[[int, int], None],
             attempts: int = 2) -> None:
    partial = destination.with_name(destination.name + ".part")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            with _open_url(url) as response, open(partial, "wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    progress(done, total)
            if total and done != total:
                raise ToolError(f"the download stopped early ({done} of {total} bytes)")
            partial.replace(destination)
            return
        except (OSError, urllib.error.URLError, ToolError) as exc:
            last = exc
            log(f"download attempt {attempt} of {url} failed: {exc}")
            time.sleep(2)
    try:
        partial.unlink(missing_ok=True)
    except OSError:
        pass
    raise ToolError(_plain(last) if last else "the download failed")


def install_tool(tool: Tool, folder: Path, progress: Callable[[int, int], None],
                 url: str | None = None, work_dir: Path | None = None) -> str:
    """Download ``tool``'s zip, check it against the published SHA-256 when there is one,
    keep its programs (and licence) in ``folder`` and return the first line of its version.
    Raises ToolError."""
    url = url or tool.url
    archive = Path(work_dir or tempfile.gettempdir()) / f"9SigmaEditor-{tool.key}.zip"
    expected = published_sha256(url + tool.checksum_suffix)
    download(url, archive, progress)
    try:
        if expected:
            if sha256_file(archive) != expected:
                raise ToolError("the download was damaged (its checksum does not match)")
            log(f"{tool.name} checksum matches {expected}")
        else:
            log(f"{tool.name}: no published checksum could be read; starting it is the check")
        work = folder.with_name(folder.name + ".new")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                wanted: dict[str, str] = {}
                for name in bundle.namelist():
                    base = name.rsplit("/", 1)[-1].lower()
                    if base in tool.programs:
                        wanted.setdefault(base, name)
                    elif base in ("license", "license.txt", "license.md"):
                        wanted.setdefault("LICENSE.txt", name)
                missing = [program for program in tool.programs if program not in wanted]
                if missing:
                    raise ToolError("the download does not contain " + " and ".join(missing))
                for program, name in wanted.items():
                    with bundle.open(name) as source, open(work / program, "wb") as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
        except zipfile.BadZipFile as exc:
            raise ToolError("the download is not a valid zip file") from exc
        if folder.exists():
            shutil.rmtree(folder)
        work.replace(folder)
    finally:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
    version = tool_version(tool, folder / tool.programs[0])
    if not version:
        raise ToolError(f"{tool.name} was unpacked but does not start")
    return version


# Processes ------------------------------------------------------------------------------------


def wait_for_process(pid: int, seconds: float) -> bool:
    """True when the process ``pid`` has ended (or never existed) within ``seconds``."""
    if sys.platform != "win32" or pid <= 0:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(seconds * 1000)) == 0
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001 - fall back to the file lock check
        return True


def locked_runtime(target: Path) -> tuple[Path, OSError] | None:
    """(path, error) for the first runtime program another process holds open (the app is
    running), or None."""
    for name in ("python.exe", "pythonw.exe"):
        exe = target / "runtime" / name
        if not exe.exists():
            continue
        try:
            with open(exe, "ab"):
                pass
        except OSError as exc:
            return exe, exc
    return None


def clean_environment() -> dict[str, str]:
    """This user's environment without what the frozen setup program set for itself (its
    temporary folder on PATH, Tcl/Tk and PyInstaller variables): the app must not see them."""
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith(("_PYI", "_MEIPASS")) or name.upper() in (
            "TCL_LIBRARY",
            "TK_LIBRARY",
            "PYINSTALLER_RESET_ENVIRONMENT",
        ):
            env.pop(name, None)
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        wanted = os.path.normcase(os.path.normpath(meipass))
        env["PATH"] = os.pathsep.join(
            part
            for part in env.get("PATH", "").split(os.pathsep)
            if part and os.path.normcase(os.path.normpath(part)) != wanted
        )
    return env


def launch_app(target: Path) -> bool:
    pythonw = target / "runtime" / "pythonw.exe"
    try:
        subprocess.Popen(
            [str(pythonw), *LAUNCH_ARGS],
            cwd=str(target),
            env=clean_environment(),
            close_fds=True,
            creationflags=DETACHED,
        )
        log("opened " + APP_NAME)
        return True
    except OSError as exc:
        log(f"could not open the app: {exc}")
        return False


def _remove(path: Path, quiet: bool = False) -> None:
    """Delete a file or a folder (a missing one is fine). ``quiet`` ignores what cannot be
    deleted; otherwise the OSError reaches the caller."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        if not quiet:
            raise


# The install ----------------------------------------------------------------------------------


@dataclass
class Step:
    key: str
    title: str
    stall_after: int  # seconds without progress before it is called out


STEPS = [
    Step("check", "Checking this computer", 30),
    Step("extract", "Unpacking the program files", 120),
    Step("install", "Putting the files in place", 120),
    Step("ffmpeg", FFMPEG.title, 120),
    Step("verify", "Checking the installed program", 240),
    Step("shortcuts", "Creating shortcuts", 60),
]

SELF_TEST = r"""
import os, shutil, sys, tempfile
from pathlib import Path
base = Path(tempfile.mkdtemp(prefix="sge-setup-test-"))
for name in ("APP_DATA", "SHARED", "PROJECTS", "EXPORTS"):
    os.environ["SGE_" + name + "_DIR"] = str(base / name.lower())
import sigma_editor
from sigma_editor.app import create_app, frontend_dist_dir
create_app()
assert frontend_dist_dir() is not None, "the app screens are missing"
import cv2, numpy, uvicorn, webview
print("self-test ok", sigma_editor.__version__)
shutil.rmtree(base, ignore_errors=True)
"""


class Engine:
    """The install itself, with no window in it. Reports through ``on_progress`` so it can
    drive the window, print to a console, or run under a test."""

    def __init__(
        self,
        payload: Payload | None,
        target: Path,
        *,
        skip_ffmpeg: bool = False,
        wait_pid: int = 0,
        register: bool = True,
        run_self_test: bool = True,
    ) -> None:
        self.payload = payload
        self.target = target
        self.staging = staging_for(target)
        self.skip_ffmpeg = skip_ffmpeg
        self.wait_pid = wait_pid
        self.register = register
        self.run_self_test = run_self_test
        self.requirements: list[Requirement] = []
        self.info = PayloadInfo()
        self.version = ""
        self.previous = installed_version(target)
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self.on_progress: Callable[[int, float, str], None] = lambda i, f, m: None
        self.cancelled = False

    def _say(self, index: int, fraction: float, message: str, logged: bool = True) -> None:
        """Report progress; ``logged=False`` for the many in-between amounts of a download or
        an unpack, which the window shows but the log does not need."""
        if logged:
            log(f"{STEPS[index].title}: {message}")
        self.on_progress(index, fraction, message)

    def run_from(self, first: int, last: int | None = None) -> None:
        """Run steps first..last inclusive. The window runs the check alone and waits for
        the click on Install; it never runs on past it by itself."""
        stop = len(STEPS) - 1 if last is None else last
        for index in range(first, stop + 1):
            if self.cancelled:
                return
            self._say(index, -1, "starting")
            getattr(self, "step_" + STEPS[index].key)(index)

    # -- steps -------------------------------------------------------------------------------

    def step_check(self, index: int) -> None:
        self._say(index, -1, "looking at this computer")
        self.requirements, self.info = check_requirements(
            self.payload, self.target, self.skip_ffmpeg
        )
        for item in self.requirements:
            log(f"  {item.status.upper():<5} {item.name:<20} {item.detail}")
        failed = [r for r in self.requirements if r.status == "fail"]
        if failed:
            first = failed[0]
            raise InstallError(
                f"{first.name}: {first.detail}",
                first.fix,
                "\n".join(f"{r.status} {r.name}: {r.detail}" for r in self.requirements),
            )
        self.version = self.info.version
        self._say(index, 1.0, "everything needed is here")

    def step_extract(self, index: int) -> None:
        assert self.payload is not None
        if self.staging.exists():
            shutil.rmtree(self.staging, ignore_errors=True)
        if self.staging.exists():
            raise InstallError(
                "The files of an earlier attempt are still in use.",
                "Wait half a minute (antivirus is usually still checking them) and click "
                "Retry. If it happens again, restart the computer and run the installer.",
                "could not remove " + str(self.staging),
            )
        self.staging.mkdir(parents=True)
        prefix = FOLDER + "/"
        root = self.staging.resolve()
        with self.payload.open_zip() as archive:
            entries = [
                e for e in archive.infolist() if e.filename.startswith(prefix) and not e.is_dir()
            ]
            total = sum(e.file_size for e in entries) or 1
            done = 0
            last = 0.0
            self._say(index, 0.0, f"0 of {total / 1048576:.0f} MB")
            for entry in entries:
                if self.cancelled:
                    return
                relative = entry.filename[len(prefix):]
                destination = self.staging / relative
                if root not in destination.resolve().parents:
                    raise InstallError(
                        "The installer contains a file outside the program folder.",
                        "Download the installer again from the release page.",
                        entry.filename,
                    )
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, open(destination, "wb") as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
                except OSError as exc:
                    raise InstallError(
                        f"Could not write {relative.split('/')[-1]}",
                        "Usually antivirus is holding the file, or the disk is full. Click "
                        "Retry; if it fails again, pause the antivirus for the install or free "
                        "some space.",
                        f"{entry.filename}\n{exc}",
                    ) from exc
                done += entry.file_size
                if time.monotonic() - last > 0.25:
                    last = time.monotonic()
                    self._say(
                        index,
                        done / total,
                        f"{done / 1048576:.0f} of {total / 1048576:.0f} MB",
                        logged=False,
                    )
        needed = (
            self.staging / "runtime" / "python.exe",
            self.staging / "app" / "backend" / "sigma_editor" / "__init__.py",
            self.staging / INFO_NAME,
        )
        missing = [str(p) for p in needed if not p.exists()]
        if missing:
            raise InstallError(
                "The unpacked files are incomplete.",
                "Download the installer again and run it.",
                "missing: " + ", ".join(missing),
            )
        try:
            raw = json.loads((self.staging / INFO_NAME).read_text(encoding="utf-8"))
            self.version = str(raw.get("version") or self.version)
        except (OSError, ValueError):
            pass
        self._say(index, 1.0, f"{total / 1048576:.0f} MB unpacked")

    def _wait_for_app(self, index: int) -> None:
        """Wait for the app that started an update (``--wait-pid``) to end, then make sure
        no copy of it is still running from this folder."""
        if self.wait_pid:
            self._say(index, -1, f"waiting for {APP_NAME} to close")
            if not wait_for_process(self.wait_pid, PID_WAIT_S):
                log(f"process {self.wait_pid} is still running after {PID_WAIT_S}s")
        deadline = time.monotonic() + APP_CLOSE_WAIT_S
        said = False
        while True:
            locked = locked_runtime(self.target)
            if locked is None:
                return
            exe, exc = locked
            if time.monotonic() >= deadline:
                raise InstallError(
                    f"{APP_NAME} is open on this computer.",
                    f"Close {APP_NAME} (check the taskbar), then click Retry.",
                    f"{exe} is in use: {exc}",
                ) from exc
            if not said:
                self._say(index, -1, f"waiting for {APP_NAME} to close")
                said = True
            time.sleep(0.5)

    def step_install(self, index: int) -> None:
        if not self.staging.is_dir():
            raise InstallError(
                "The unpacked files are gone.", "Click Retry to unpack them again.",
                str(self.staging)
            )
        self._wait_for_app(index)
        target = self.target
        target.mkdir(parents=True, exist_ok=True)
        carried: list[tuple[Path, Path]] = []
        for tool in TOOLS:
            kept_old = target / "runtime" / tool.folder[0]
            kept_new = self.staging / "runtime" / tool.folder[0]
            if kept_old.is_dir() and not kept_new.exists():
                kept_old.replace(kept_new)  # the same drive: a rename, nothing is copied
                carried.append((kept_old, kept_new))
                log(f"{tool.name} carried over from the previous version")
        moved: list[tuple[Path, Path]] = []
        placed: list[Path] = []
        try:
            self._say(index, 0.1, "moving the previous version aside")
            for name in PROGRAM_DIRS + PROGRAM_FILES:
                old = target / name
                aside = target / (name + ".old")
                _remove(aside)
                if old.exists():
                    old.replace(aside)
                    moved.append((old, aside))
            items = sorted(self.staging.iterdir())
            for number, item in enumerate(items, start=1):
                self._say(index, 0.2 + 0.7 * number / len(items), "placing " + item.name)
                destination = target / item.name
                _remove(destination)
                item.replace(destination)
                placed.append(destination)
        except OSError as exc:
            log(f"placing failed, putting the previous version back: {exc}")
            for path in placed:
                try:
                    path.replace(self.staging / path.name)
                except OSError:
                    _remove(path, quiet=True)
            for old, aside in reversed(moved):
                try:
                    if not old.exists():
                        aside.replace(old)
                except OSError as undo:
                    log(f"could not put {old} back: {undo}")
            for kept_old, kept_new in carried:
                if not kept_old.exists() and kept_new.exists():
                    try:
                        kept_new.replace(kept_old)
                    except OSError:
                        pass
            raise InstallError(
                "Could not replace the program files.",
                f"Close {APP_NAME} if it is open, then click Retry. If it is not open, restart "
                "the computer and run the installer again.",
                str(exc),
            ) from exc
        for _, aside in moved:
            _remove(aside, quiet=True)
            if aside.exists():
                log(f"{aside} could not be removed yet; the next install removes it")
        shutil.rmtree(self.staging, ignore_errors=True)
        self._say(index, 1.0, "files in place")

    def step_ffmpeg(self, index: int) -> None:
        self._tool_step(index, FFMPEG, self.skip_ffmpeg)

    def _tool_step(self, index: int, tool: Tool, skipped: bool) -> None:
        """Keep a working copy, else download it; when that fails use one already on PATH,
        and when there is none the install still finishes, with a warning that says what
        the app cannot do and what to do."""
        if skipped:
            self._say(index, 1.0, f"skipped (--skip-{tool.key})")
            return
        folder = tool.folder_in(self.target)
        current = tool_version(tool, folder / tool.programs[0])
        if current and all((folder / program).is_file() for program in tool.programs):
            self._say(index, 1.0, "already installed: " + current)
            return
        shown = {"at": 0.0, "logged": -1}

        def progress(done: int, total: int) -> None:
            if time.monotonic() - shown["at"] < 0.5 and done != total:
                return
            shown["at"] = time.monotonic()
            tens = done // (10 * 1048576)
            logged = tens != shown["logged"] or done == total
            shown["logged"] = tens
            if total:
                self._say(index, done / total,
                          f"downloading: {done / 1048576:.0f} of {total / 1048576:.0f} MB",
                          logged=logged)
            else:
                self._say(index, -1, f"downloading: {done / 1048576:.0f} MB", logged=logged)

        self._say(index, -1, f"connecting to {tool.source}")
        try:
            version = install_tool(tool, folder, progress)
            self._say(index, 1.0, "installed: " + version)
            return
        except ToolError as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - a missing helper must not fail the install
            reason = _plain(exc)
            log(traceback.format_exc())
        log(f"{tool.name} download failed: {reason}")
        on_path = shutil.which(tool.command)
        found = tool_version(tool, on_path) if on_path else ""
        if found:
            note = (
                f"{tool.name} could not be downloaded ({reason}), so the app uses the "
                f"{tool.name} already on this computer: {on_path}"
            )
            self.notes.append(note)
            self._say(index, 1.0, note)
        else:
            warning = (
                f"{tool.name} could not be downloaded ({reason}). {APP_NAME} is installed, but "
                f"it needs {tool.name} {tool.needed_for}. Connect to the internet and run this "
                "installer again: it keeps everything else."
            )
            self.warnings.append(warning)
            self._say(index, 1.0, f"{tool.name} is missing")

    def step_verify(self, index: int) -> None:
        if not self.run_self_test:
            self._say(index, 1.0, "skipped")
            return
        python = self.target / "runtime" / "python.exe"
        self._say(index, -1, "starting the program's own self-test (the first start is slower)")
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {
            "SystemRoot": system_root,
            "PATH": str(Path(system_root) / "System32"),
            "TEMP": tempfile.gettempdir(),
            "TMP": tempfile.gettempdir(),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
            "APPDATA": os.environ.get("APPDATA", ""),
        }
        try:
            result = subprocess.run(
                [str(python), "-c", SELF_TEST],
                cwd=str(self.target),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SELF_TEST_TIMEOUT_S,
                creationflags=NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallError(
                "The self-test did not finish.",
                "Click Retry. If it happens again, send the log file.",
                str(exc),
            ) from exc
        except OSError as exc:
            raise InstallError(
                "The installed program could not be started.",
                "Antivirus may have blocked it. Run the installer again; if it happens again, "
                "send the log file.",
                f"{python}\n{exc}",
            ) from exc
        log("self-test output:\n" + result.stdout + result.stderr)
        if result.returncode != 0 or "self-test ok" not in result.stdout:
            last = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
            raise InstallError(
                "The installed program did not pass its self-test: " + last[0][:200],
                "Click Retry once. If it fails again, click Copy error details and send them.",
                result.stdout + result.stderr,
            )
        self._say(index, 1.0, "self-test passed")

    def step_shortcuts(self, index: int) -> None:
        try:
            command = write_uninstaller(self.payload, self.target)
        except OSError as exc:
            log(f"could not write {UNINSTALLER}: {exc}")
            command = ""
        if not self.register:
            self._say(index, 1.0, "left as they are (an install into another folder)")
            self._copy_log()
            return
        self._say(index, -1, "desktop and Start menu")
        try:
            create_shortcuts(self.target)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(
                "The shortcuts could not be created.",
                f"{APP_NAME} is installed. Click Retry, or start it with 9SigmaEditor.cmd in "
                f"{self.target}.",
                str(exc),
            ) from exc
        self._say(index, 0.6, "uninstall entry")
        try:
            if command:
                write_uninstall_entry(self.target, self.version, command)
        except OSError as exc:
            # The app works without it; say so in the log rather than fail the install.
            log(f"could not write the uninstall entry: {exc}")
            self.notes.append("The entry in Settings > Apps could not be written.")
        self._copy_log()
        self._say(index, 1.0, "shortcuts on the desktop and in the Start menu")

    def _copy_log(self) -> None:
        try:
            shutil.copy2(LOG_PATH, self.target / "install.log")
        except OSError:
            pass


# Uninstall ------------------------------------------------------------------------------------


def uninstall(target: Path, wait_seconds: float = 30.0) -> tuple[bool, str]:
    """Remove the program, its shortcuts and its uninstall entry. App data, projects and
    exports are elsewhere and stay. Returns (removed everything, message)."""
    deadline = time.monotonic() + wait_seconds
    while locked_runtime(target) is not None:
        if time.monotonic() >= deadline:
            return False, f"{APP_NAME} is still open. Close it, then uninstall again."
        time.sleep(0.5)
    gone = remove_shortcuts(target)
    log(f"shortcuts removed: {gone or 'none'}")
    log(f"uninstall entry removed: {remove_uninstall_entry(target)}")
    for folder in (staging_for(target), target):
        for _ in range(5):
            if not folder.exists():
                break
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                time.sleep(1)
    if target.exists():
        return False, (
            f"{APP_NAME} was removed except for some files in {target} that are in use. "
            "Restart the computer and delete that folder."
        )
    return True, (
        f"{APP_NAME} was removed from this computer. Your projects, exports and settings are "
        "kept (Videos\\9SigmaEditor and AppData\\Local\\9SigmaEditor)."
    )


def schedule_self_delete(path: Path) -> None:
    try:
        cmd = system_program("cmd.exe")
        subprocess.Popen(
            f'"{cmd}" /c ping -n 4 127.0.0.1 >nul & del /f /q "{path}"',
            creationflags=NO_WINDOW | DETACHED,
            close_fds=True,
        )
    except OSError:
        pass


def run_uninstall(argv: list[str], target: Path, unattended: bool) -> int:
    exe = Path(sys.executable)
    frozen = getattr(sys, "frozen", False)
    if frozen and "--from-temp" not in argv and same_folder(exe.parent, target):
        # A program cannot delete the folder it runs from: run a copy from the temp folder.
        copy = Path(tempfile.gettempdir()) / f"9SigmaEditor-uninstall-{os.getpid()}.exe"
        shutil.copy2(exe, copy)
        args = [str(copy), "--uninstall", "--target", str(target), "--from-temp"]
        if unattended:
            args.append("--unattended")
        env = dict(clean_environment(), PYINSTALLER_RESET_ENVIRONMENT="1")
        release_instance()  # the copy takes the one-installer-at-a-time name over
        subprocess.Popen(args, close_fds=True, creationflags=DETACHED, env=env,
                         cwd=tempfile.gettempdir())
        return 0
    if not (target / "runtime").exists() and not (target / INFO_NAME).exists():
        message = f"{APP_NAME} is not installed in {target}."
        if not unattended:
            _message(message, error=True)
        print(message)
        return 1
    if not unattended and not _ask(
        f"Remove {APP_NAME} from this computer?\n\nYour projects, exports and settings are "
        "kept."
    ):
        return 0
    while True:
        ok, message = uninstall(target)
        log(message)
        if ok or unattended or not message.startswith(f"{APP_NAME} is still open"):
            break
        if not _ask(message + "\n\nTry again now?"):
            break
    if unattended:
        print(message)
    else:
        _message(message, error=not ok)
    if frozen and "--from-temp" in argv:
        schedule_self_delete(exe)
    return 0 if ok else 1


def _ask(text: str) -> bool:
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x04 | 0x20) == 6  # YES
    except Exception:  # noqa: BLE001
        return True


def _message(text: str, error: bool = False) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10 if error else 0x40)
    except Exception:  # noqa: BLE001
        print(text)


# Unattended -----------------------------------------------------------------------------------


def attach_console() -> None:
    """A windowed program started from a console prints there (best effort)."""
    if sys.stdout is not None or sys.platform != "win32":
        return
    try:
        if ctypes.windll.kernel32.AttachConsole(-1):
            stream = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            sys.stdout = sys.stderr = stream
    except Exception:  # noqa: BLE001
        pass


def run_unattended(engine: Engine, open_app: bool) -> int:
    def show(index: int, fraction: float, message: str) -> None:
        if message != "starting":
            print(f"  {STEPS[index].title:<34} {message}", flush=True)

    engine.on_progress = show
    try:
        engine.run_from(0)
    except InstallError as exc:
        print(f"FAILED: {exc.what}\n  Do this: {exc.fix}\n  {exc.detail[:600]}", flush=True)
        log(f"FAILED: {exc.what} | {exc.fix} | {exc.detail[:2000]}")
        return 1
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        log("FAILED unexpectedly:\n" + detail)
        print("FAILED unexpectedly:\n" + detail, flush=True)
        return 1
    for line in engine.notes + engine.warnings:
        print("  NOTE: " + line, flush=True)
    print(f"Installed {APP_NAME} {engine.version} in {engine.target}. Log: {LOG_PATH}",
          flush=True)
    log("installation completed")
    if open_app:
        launch_app(engine.target)
    return 0


# The window -----------------------------------------------------------------------------------


def resource(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent / "assets" / "icons"
    base = Path(getattr(sys, "_MEIPASS", here))
    return base / name


def run_window(engine: Engine, auto: bool, open_app: bool) -> int:  # noqa: C901 - one window
    import tkinter as tk
    from tkinter import ttk

    navy, gold, muted = "#0B1220", "#FFC000", "#5C6675"
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    state: dict[str, Any] = {
        "step": -1,
        "error": None,
        "running": False,
        "last": time.monotonic(),
        "retries": 0,
        "done": False,
        "stalled": False,
    }

    root = tk.Tk()
    root.title(f"{APP_NAME} Setup")
    root.geometry("660x540")
    root.minsize(600, 480)
    icon = resource("sigma.ico")
    if icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass
    style = ttk.Style(root)
    try:
        style.configure("Status.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Detail.TLabel", foreground=muted)
        style.configure("Small.TLabel", foreground=muted, font=("Segoe UI", 8))
        style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
    except tk.TclError:
        pass

    header = tk.Frame(root, bg=navy, height=64)
    header.pack(fill="x")
    tk.Label(header, text=APP_NAME, bg=navy, fg=gold, font=("Segoe UI", 16, "bold")).pack(
        side="left", padx=20, pady=14
    )
    version_label = tk.Label(header, text="Setup", bg=navy, fg="#C9D1DC", font=("Segoe UI", 10))
    version_label.pack(side="left", pady=18)

    body = ttk.Frame(root, padding=(20, 14, 20, 8))
    body.pack(fill="both", expand=True)
    status = ttk.Label(body, text="Preparing...", style="Status.TLabel")
    status.pack(anchor="w")
    detail = ttk.Label(body, text="Reading the installer and looking at this computer.",
                       style="Detail.TLabel", wraplength=600, justify="left")
    detail.pack(anchor="w", pady=(2, 8))
    bar = ttk.Progressbar(body, mode="indeterminate", maximum=1000)
    bar.pack(fill="x")
    bar.start(12)

    table = ttk.Treeview(body, columns=("status", "detail"), height=8, selectmode="none")
    table.heading("#0", text="Item")
    table.heading("status", text="Status")
    table.heading("detail", text="Details")
    table.column("#0", width=130, stretch=False)
    table.column("status", width=80, stretch=False)
    table.column("detail", width=380)
    table_shown = {"value": False}

    next_box = ttk.LabelFrame(body, text="What to do next", padding=(10, 6))
    next_box.pack(fill="x", side="bottom", pady=(10, 0))
    next_text = ttk.Label(next_box, text="Wait. Nothing needs clicking yet.", wraplength=590,
                          justify="left")
    next_text.pack(anchor="w")

    footer = ttk.Frame(root, padding=(20, 0, 20, 14))
    footer.pack(fill="x", side="bottom")
    ttk.Label(footer, text=f"Log: {LOG_PATH}", style="Small.TLabel").pack(anchor="w", pady=(0, 8))
    buttons = ttk.Frame(footer)
    buttons.pack(fill="x")

    def view_log() -> None:
        try:
            os.startfile(str(LOG_PATH))  # type: ignore[attr-defined]  # noqa: S606
        except OSError:
            pass

    def copy_error() -> None:
        error = state["error"]
        text = "{} setup stopped at: {}\n{}\n\nDo this: {}\n\nDetail:\n{}\n\nLog: {}".format(
            APP_NAME,
            STEPS[max(0, state["step"])].title,
            error.what if error else "",
            error.fix if error else "",
            error.detail if error else "",
            LOG_PATH,
        )
        root.clipboard_clear()
        root.clipboard_append(text)
        next_text.configure(
            text="Copied. Paste it into a message to whoever looks after the app. "
            + (error.fix if error else "")
        )

    btn_log = ttk.Button(buttons, text="View log", command=view_log)
    btn_copy = ttk.Button(buttons, text="Copy error details", command=copy_error)
    btn_close = ttk.Button(buttons, text="Close", command=lambda: close())
    btn_open = ttk.Button(buttons, text=f"Open {APP_NAME}", style="Primary.TButton",
                          command=lambda: open_now())
    btn_install = ttk.Button(buttons, text="Install", style="Primary.TButton",
                             command=lambda: install())
    btn_retry = ttk.Button(buttons, text="Retry", command=lambda: retry())

    def show_buttons(**which: bool) -> None:
        for button in (btn_log, btn_copy, btn_close, btn_open, btn_install, btn_retry):
            button.pack_forget()
        btn_log.pack(side="left")
        if which.get("copy"):
            btn_copy.pack(side="left", padx=(8, 0))
        if which.get("close", True):
            btn_close.pack(side="right")
        for name, button in (("open", btn_open), ("install", btn_install), ("retry", btn_retry)):
            if which.get(name):
                button.pack(side="right", padx=(0, 8))

    def set_bar(fraction: float) -> None:
        if fraction < 0:
            if str(bar.cget("mode")) != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start(12)
        else:
            if str(bar.cget("mode")) != "determinate":
                bar.stop()
                bar.configure(mode="determinate")
            bar.configure(value=int(max(0.0, min(1.0, fraction)) * 1000))

    def show_table(visible: bool) -> None:
        if visible and not table_shown["value"]:
            table.pack(fill="both", expand=True, pady=(10, 0))
        elif not visible and table_shown["value"]:
            table.pack_forget()
        table_shown["value"] = visible

    def fill_table() -> None:
        table.delete(*table.get_children())
        marks = {"ok": "OK", "warn": "Note", "fail": "Problem"}
        for item in engine.requirements:
            table.insert("", "end", text=item.name, values=(marks[item.status], item.detail))

    def run(first: int, last: int | None) -> None:
        if state["running"]:
            return
        state["running"] = True
        state["error"] = None

        def work() -> None:
            try:
                engine.run_from(first, last)
            except InstallError as exc:
                events.put(("failed", exc))
                return
            except Exception:  # noqa: BLE001
                events.put(
                    (
                        "failed",
                        InstallError(
                            "Something unexpected went wrong.",
                            "Click Retry once. If it happens again, click Copy error details "
                            "and send them.",
                            traceback.format_exc(),
                        ),
                    )
                )
                return
            events.put(("checked" if last is not None and last < len(STEPS) - 1 else "done", None))

        engine.on_progress = lambda i, f, m: events.put(("progress", (i, f, m)))
        threading.Thread(target=work, name="setup", daemon=True).start()

    def install() -> None:
        if state["error"] is not None and state["step"] == 0:
            return
        show_table(False)
        show_buttons(close=False)
        next_text.configure(text="Wait. Leave this window open until it says the installation "
                                 "is complete. This takes two to five minutes.")
        run(1, None)

    def retry() -> None:
        first = max(0, state["step"])
        show_table(False)
        show_buttons(close=False)
        next_text.configure(text=f"Trying again from '{STEPS[first].title}'. Wait.")
        run(first, 0 if first == 0 else None)

    def open_now() -> None:
        if launch_app(engine.target):
            close(force=True)
        else:
            next_text.configure(text=f"{APP_NAME} did not start. Use the desktop shortcut, or "
                                     "send the log file.")

    def close(force: bool = False) -> None:
        if state["running"] and not force:
            from tkinter import messagebox

            if not messagebox.askyesno(
                f"{APP_NAME} Setup",
                "Setup is still working. Closing now can leave the program half installed "
                "(running the installer again fixes that). Close anyway?",
            ):
                return
        root.destroy()

    def on_progress(index: int, fraction: float, message: str) -> None:
        state["step"] = index
        state["last"] = time.monotonic()
        if state["stalled"]:
            state["stalled"] = False
            next_text.configure(text="Wait. Leave this window open until it says the "
                                     "installation is complete.")
        status.configure(text=f"Step {index + 1} of {len(STEPS)}: {STEPS[index].title}")
        if message != "starting":
            detail.configure(text=message)
        set_bar(fraction)
        if engine.version:
            version_label.configure(text=f"Setup, version {engine.version}")

    def on_checked() -> None:
        fill_table()
        show_table(True)
        set_bar(0)
        status.configure(text="Ready to install")
        previous = engine.previous
        detail.configure(
            text=(f"Version {previous} is installed and will be updated to {engine.version}. "
                  if previous else "")
            + f"It goes into {engine.target}; no administrator password is needed."
        )
        next_text.configure(text="Click Install.")
        show_buttons(install=True)
        if auto:
            root.after(300, install)

    def on_failed(error: InstallError) -> None:
        state["error"] = error
        log("FAILED at {}: {} | fix: {} | {}".format(
            STEPS[max(0, state["step"])].title, error.what, error.fix, error.detail[:2000]))
        set_bar(0)
        status.configure(text="Could not finish: " + STEPS[max(0, state["step"])].title.lower())
        detail.configure(text=error.what)
        next_text.configure(text=error.fix)
        if state["step"] == 0:
            fill_table()
            show_table(True)
            btn_retry.configure(text="Check again")
        else:
            btn_retry.configure(text="Retry")
        show_buttons(copy=True, retry=True)
        if auto and state["retries"] < 1:
            # One automatic retry for an update the app started; a second failure stays on
            # screen with Retry and Close: an update must never vanish without a word.
            state["retries"] += 1
            root.after(1500, retry)

    def on_done() -> None:
        state["done"] = True
        set_bar(1.0)
        status.configure(text="Installation completed")
        version = f" {engine.version}" if engine.version else ""
        words = f"{APP_NAME}{version} is installed for {os.environ.get('USERNAME', 'you')}."
        message = ""
        if engine.warnings:
            message = " ".join(engine.warnings)
        elif engine.notes:
            message = " ".join(engine.notes)
        detail.configure(text=words)
        log("installation completed")
        if open_app:
            if launch_app(engine.target):
                next_text.configure(text=(message + " " if message else "")
                                    + f"{APP_NAME} is opening. You can close this window.")
                show_buttons()
                if auto and not engine.warnings:
                    root.after(2500, lambda: close(force=True))
                return
            message = (message + " " if message else "") + (
                f"{APP_NAME} did not start by itself. Click Open {APP_NAME}."
            )
        next_text.configure(text=message or f"Click Open {APP_NAME} to sign in.")
        show_buttons(open=True)

    def pump() -> None:
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "progress":
                    on_progress(*value)
                elif kind == "failed":
                    state["running"] = False
                    on_failed(value)
                elif kind == "checked":
                    state["running"] = False
                    on_checked()
                elif kind == "done":
                    state["running"] = False
                    on_done()
        except queue.Empty:
            pass
        if state["running"] and state["step"] >= 0:
            quiet = time.monotonic() - state["last"]
            if quiet > STEPS[state["step"]].stall_after:
                state["stalled"] = True
                next_text.configure(
                    text=f"Still working on '{STEPS[state['step']].title.lower()}' - no change "
                    f"for {int(quiet)} s. This is usually antivirus checking the new files, and "
                    "it does finish. Leave the window open. If nothing changes for ten minutes, "
                    "close it, restart the computer and run the installer again."
                )
        root.after(100, pump)

    root.protocol("WM_DELETE_WINDOW", close)
    show_buttons()
    root.after(50, lambda: (status.configure(text=STEPS[0].title + "..."), run(0, 0)))
    root.after(100, pump)
    if auto:
        status.configure(text=f"Updating {APP_NAME}")
    root.mainloop()
    if state["done"]:
        return 0
    return 1 if state["error"] is not None else 2


# Main -----------------------------------------------------------------------------------------


def _value(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        at = argv.index(flag)
        if at + 1 < len(argv):
            return argv[at + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    unattended = "--unattended" in argv or "--silent" in argv
    if unattended:
        attach_console()
    global LOG_PATH
    explicit_target = _value(argv, "--target")
    target = Path(explicit_target) if explicit_target else default_target()
    if "--uninstall" in argv:
        LOG_PATH = LOG_PATH.with_name("9SigmaEditor-uninstall.log")
        here = Path(sys.executable).resolve().parent
        if not explicit_target and getattr(sys, "frozen", False) and (here / INFO_NAME).exists():
            target = here  # uninstall.exe sits in the install folder
    target = Path(os.path.abspath(target))

    if not claim_instance():
        message = f"The {APP_NAME} installer is already running. Wait for it to finish."
        if unattended:
            print(message)
        elif "--auto" not in argv:
            _message(message)
        return 3

    if "--uninstall" in argv:
        start_log(target, argv)
        return run_uninstall(argv, target, unattended)

    start_log(target, argv)
    payload = find_payload(_value(argv, "--payload"))
    log(f"payload: {payload.describe if payload else 'NONE'}")
    try:
        wait_pid = int(_value(argv, "--wait-pid") or 0)
    except ValueError:
        wait_pid = 0
    register = (
        explicit_target is None
        or same_folder(target, default_target())
        or same_folder(registered_location(), target)
        or "--shortcuts" in argv
    )
    engine = Engine(
        payload,
        target,
        skip_ffmpeg="--skip-ffmpeg" in argv,
        wait_pid=wait_pid,
        register=register,
    )
    open_app = "--open" in argv
    if unattended:
        return run_unattended(engine, open_app=open_app)
    try:
        # The window always opens the app at the end; --open matters for --unattended.
        return run_window(engine, auto="--auto" in argv, open_app=True)
    except Exception:  # noqa: BLE001 - last resort: say something on screen
        detail = traceback.format_exc()
        log("window failed: " + detail)
        last = detail.strip().splitlines()[-1][:300]
        _message(f"The setup window could not open.\n\nLog: {LOG_PATH}\n\n{last}", error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
