"""Assemble dist/9SigmaEditor: the whole app with its own Python, as the installer ships it.

    .venv\\Scripts\\python.exe tools\\build_runtime.py
    .venv\\Scripts\\python.exe tools\\build_runtime.py --cache F:\\somewhere\\cache --no-parallax

Produces ``dist/9SigmaEditor/`` (docs/RELEASE-CONTRACT.md section 2):

    VERSION, install-info.json      the version from pyproject.toml, how and when it was built
    9SigmaEditor.cmd               opens the app window
    9SigmaEditor-Server.cmd        runs only the server in a console window (support, testing)
    runtime\\                        python.org's embeddable Python 3.12 with every dependency
    app\\                            the package, the built screens, config, fonts, the icon

Why an embedded runtime (the approach of the owner's earlier app, SceneForge): a team laptop then
needs nothing installed first -- no Python, no pip, no Node, no administrator rights -- and setup
is a copy. Everything that can fail while installing packages fails here, on the build machine,
where someone can read the error.

* The embeddable zip is downloaded once into the cache folder and checked against the SHA-256
  recorded below; drop it in by hand on a machine without internet.
* Dependencies come from pyproject.toml (no dev extras) and are installed with the build
  machine's pip into ``runtime\\Lib\\site-packages``, pinned to the versions in the virtual
  environment running this script (``pip freeze``): the shipped app runs what the tests ran on.
* The package itself is copied as source into ``app\\backend`` (the code finds config, fonts and
  the built screens relative to the repository layout, so ``app\\`` mirrors it) and reached
  through ``python312._pth``.
* The Visual C++ runtime DLLs are copied beside python.exe (app-local, supported by Microsoft for
  these files): OpenCV and onnxruntime need them, and a laptop without the redistributable then
  still works.
* FFmpeg is not bundled: the installer downloads it into ``runtime\\ffmpeg``
  (docs/RELEASE-CONTRACT.md section 3), and ``sitecustomize.py`` puts it on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
OUT = DIST / "9SigmaEditor"
RUNTIME = OUT / "runtime"
SITE = RUNTIME / "Lib" / "site-packages"
APP = OUT / "app"
FRONTEND = ROOT / "frontend"

PY_VERSION = "3.12.10"
"""The last 3.12 release python.org built Windows binaries for (later 3.12 releases are
source-only security fixes). It matches the build machine's Python, so the wheels pip picks
here are the ones the runtime needs."""
EMBED_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{EMBED_NAME}"
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
"""Recorded on 2026-10-11 from the python.org download; its MD5 matched the one python.org
publishes for the file (fe8ef205f2e9c3ba44d0cf9954e1abd3)."""

PTH = "python312.zip\n.\n..\\app\\backend\nLib\\site-packages\nimport site\n"
"""The embeddable Python takes its whole sys.path from this file and ignores PYTHONPATH: the
standard library, the runtime folder (sitecustomize.py), the app's package and the
dependencies. ``import site`` makes the .pth files of the dependencies work."""

VC_DLLS = (
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "concrt140.dll",
    "vccorlib140.dll",
)
"""vcruntime140.dll and vcruntime140_1.dll come with the embeddable Python (a newer build than
most laptops have), so they are not replaced."""

SITECUSTOMIZE = '''"""Run by the 9Sigma Editor runtime before anything else (site.py imports it).

Puts the FFmpeg the installer downloaded (runtime/ffmpeg/bin) first on PATH, so the app and
its health checks find it without anyone setting a path. A path saved in Settings (FFMPEG_PATH)
still wins: the app looks there before PATH.
"""

import os

TOOLS = (("ffmpeg", "bin", "ffmpeg.exe"),)


def _bundled_tools():
    here = os.path.dirname(os.path.abspath(__file__))
    for *parts, program in TOOLS:
        folder = os.path.join(here, *parts)
        if os.path.isfile(os.path.join(folder, program)):
            yield folder


try:
    for _folder in reversed(list(_bundled_tools())):
        if _folder not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = _folder + os.pathsep + os.environ.get("PATH", "")
except Exception:  # noqa: BLE001 - never stop the app from starting over this
    pass
'''

APP_LAUNCHER = (
    "@echo off\r\n"
    "rem Opens 9Sigma Editor in its own window. The desktop shortcut does the same.\r\n"
    'start "" "%~dp0runtime\\pythonw.exe" -m sigma_editor.launch %*\r\n'
)
SERVER_LAUNCHER = (
    "@echo off\r\n"
    "rem Runs only the 9Sigma Editor server in this window, for support and testing.\r\n"
    "rem Open http://127.0.0.1:8766 in a web browser; close this window to stop it.\r\n"
    "rem Another port: 9SigmaEditor-Server.cmd --port 8780\r\n"
    "title 9Sigma Editor server\r\n"
    '"%~dp0runtime\\python.exe" -m sigma_editor.cli serve %*\r\n'
    "if errorlevel 1 pause\r\n"
)

SMOKE_TEST = r"""
import json, os, sys, tempfile
from pathlib import Path
base = Path(tempfile.mkdtemp(prefix="sge-smoke-"))
for name in ("APP_DATA", "SHARED", "PROJECTS", "EXPORTS"):
    os.environ["SGE_" + name + "_DIR"] = str(base / name.lower())
import sigma_editor
from sigma_editor.app import create_app, frontend_dist_dir
app = create_app()
assert frontend_dist_dir() is not None, "the built screens were not found"
import cv2, numpy, PIL, pydantic, uvicorn, webview
import clr  # pythonnet: what the app window needs on Windows
clr.AddReference("System.Windows.Forms")
from sigma_editor.render.fonts import missing_fonts
assert not missing_fonts(), missing_fonts()
info = {"ok": True, "python": sys.version.split()[0], "app": sigma_editor.__version__,
        "package": sigma_editor.__file__, "opencv": cv2.__version__,
        "numpy": numpy.__version__, "pywebview": getattr(webview, "__version__", "?")}
try:
    import onnxruntime
    info["onnxruntime"] = onnxruntime.__version__
except ImportError:
    info["onnxruntime"] = None
print(json.dumps(info))
import shutil
shutil.rmtree(base, ignore_errors=True)
"""


def say(text: str) -> None:
    print("  " + text, flush=True)


def fail(text: str) -> SystemExit:
    return SystemExit("\n  BUILD STOPPED: " + text + "\n")


# Version -------------------------------------------------------------------------------------


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def read_version() -> str:
    """The version in pyproject.toml, which must equal ``__version__`` in the package."""
    version = str(project()["version"])
    init = (ROOT / "backend" / "sigma_editor" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init, re.MULTILINE)
    package_version = match.group(1) if match else "?"
    if package_version != version:
        raise fail(
            f"pyproject.toml says {version} but backend/sigma_editor/__init__.py says "
            f"{package_version}. Change both to the new version, then build again."
        )
    return version


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


# Python ----------------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_embed(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / EMBED_NAME
    if not archive.exists():
        say("downloading " + EMBED_URL)
        partial = archive.with_suffix(".part")
        with urllib.request.urlopen(EMBED_URL, timeout=120) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(archive)
    actual = sha256_of(archive)
    if actual != EMBED_SHA256:
        raise fail(
            f"{archive} has SHA-256 {actual}, expected {EMBED_SHA256}. Delete it and build again "
            "(or download it from python.org by hand)."
        )
    say(f"embeddable Python {PY_VERSION}: {archive} (SHA-256 checked)")
    return archive


def unpack_runtime(archive: Path) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    RUNTIME.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(RUNTIME)
    pth = next(RUNTIME.glob("python3*._pth"))
    pth.write_bytes(PTH.replace("\n", "\r\n").encode("ascii"))
    SITE.mkdir(parents=True)
    (RUNTIME / "sitecustomize.py").write_bytes(SITECUSTOMIZE.encode("utf-8"))
    say(f"unpacked Python {PY_VERSION} into {RUNTIME.relative_to(ROOT)}; site enabled")


def requirements(parallax: bool) -> list[str]:
    meta = project()
    wanted = list(meta.get("dependencies", []))
    if parallax:
        wanted += list(meta.get("optional-dependencies", {}).get("parallax", []))
    return wanted


def write_constraints() -> Path | None:
    """Pins from the virtual environment running this script, so the runtime gets the
    versions the tests ran against. Local and editable installs are left out."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable",
         "--disable-pip-version-check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        say("warning: pip freeze failed; dependencies are resolved without pins")
        return None
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(("-e", "#")) and " @ " not in line
        and not line.lower().startswith("9sigma-editor")
    ]
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "constraints.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"pinned to the {len(lines)} versions in {Path(sys.executable).parent.parent}")
    return path


def install_packages(parallax: bool, pin: bool) -> None:
    wanted = requirements(parallax)
    say(f"installing {len(wanted)} requirements (parallax add-on: {'yes' if parallax else 'no'})")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(SITE),
        "--no-warn-script-location",
        "--disable-pip-version-check",
        "--prefer-binary",
        "--quiet",
    ]
    constraints = write_constraints() if pin else None
    if constraints is not None:
        command += ["--constraint", str(constraints)]
    started = time.time()
    if subprocess.run(command + wanted, check=False).returncode != 0:
        raise fail("pip could not install the dependencies into the runtime (see above)")
    # Console scripts point at the build machine's Python; the app never runs them.
    shutil.rmtree(SITE / "bin", ignore_errors=True)
    say(f"dependencies installed in {time.time() - started:.0f}s")


def installed_packages() -> list[str]:
    found = []
    for info in sorted(SITE.glob("*.dist-info")):
        name, _, version = info.name[: -len(".dist-info")].partition("-")
        found.append(f"{name}=={version}")
    return found


def copy_vc_runtime() -> None:
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    copied = []
    for name in VC_DLLS:
        source = system32 / name
        if source.exists() and not (RUNTIME / name).exists():
            shutil.copy2(source, RUNTIME / name)
            copied.append(name)
    missing = [name for name in VC_DLLS[:3] if not (RUNTIME / name).exists()]
    if missing:
        raise fail(
            "the Visual C++ runtime is not installed on this build machine "
            f"({', '.join(missing)} missing in {system32}). Install the Microsoft Visual C++ "
            "Redistributable (x64) and build again."
        )
    say("Visual C++ runtime beside python.exe: " + ", ".join(copied))


# The app ---------------------------------------------------------------------------------------


def _ignore_caches(_folder: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def copy_app() -> None:
    backend = ROOT / "backend" / "sigma_editor"
    shutil.copytree(backend, APP / "backend" / "sigma_editor", ignore=_ignore_caches)
    config = APP / "config"
    config.mkdir(parents=True)
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if not path.name.startswith("local."):  # config/local.* is the owner's own, git-ignored
            shutil.copy2(path, config / path.name)
    shutil.copytree(ROOT / "assets" / "fonts", APP / "assets" / "fonts")
    shutil.copytree(ROOT / "assets" / "icons", APP / "assets" / "icons")
    shutil.copy2(ROOT / "pyproject.toml", APP / "pyproject.toml")
    say("app copied: backend/sigma_editor, config, assets")


def _newest(paths: list[Path]) -> float:
    newest = 0.0
    for base in paths:
        if base.is_file():
            newest = max(newest, base.stat().st_mtime)
        elif base.is_dir():
            for path in base.rglob("*"):
                if path.is_file():
                    newest = max(newest, path.stat().st_mtime)
    return newest


def npm() -> str:
    found = shutil.which("npm.cmd") or shutil.which("npm")
    if found:
        return found
    default = Path(r"C:\Program Files\nodejs\npm.cmd")
    if default.exists():
        return str(default)
    raise fail("Node.js (npm) is needed to build the screens: install it from nodejs.org")


def build_frontend(force: bool, skip: bool) -> None:
    dist = FRONTEND / "dist"
    index = dist / "index.html"
    sources = [
        FRONTEND / "src",
        FRONTEND / "public",
        FRONTEND / "index.html",
        FRONTEND / "package.json",
        FRONTEND / "vite.config.ts",
    ]
    stale = not index.exists() or _newest(sources) > index.stat().st_mtime
    if skip:
        if not index.exists():
            raise fail("frontend/dist is missing and --skip-frontend was given")
        if stale:
            say("WARNING: frontend/dist is older than frontend/src (kept: --skip-frontend)")
    elif force or stale:
        tool = npm()
        node_dir = str(Path(tool).parent)
        env = dict(os.environ, PATH=node_dir + os.pathsep + os.environ.get("PATH", ""))
        if not (FRONTEND / "node_modules").is_dir():
            say("npm ci (first build of the screens on this machine)")
            subprocess.run([tool, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND, env=env,
                           check=True)
        say("building the screens (npm run build)")
        subprocess.run([tool, "run", "build"], cwd=FRONTEND, env=env, check=True)
    else:
        say("frontend/dist is up to date")
    shutil.copytree(dist, APP / "frontend" / "dist")


def write_launchers_and_info(version: str, parallax: bool) -> dict:
    (OUT / "9SigmaEditor.cmd").write_bytes(APP_LAUNCHER.encode("ascii"))
    (OUT / "9SigmaEditor-Server.cmd").write_bytes(SERVER_LAUNCHER.encode("ascii"))
    (OUT / "VERSION").write_text(version + "\n", encoding="ascii")
    dirty = bool(git("status", "--porcelain", "--", "backend", "frontend/src", "config",
                     "assets", "pyproject.toml"))
    info = {
        "product": "9Sigma Editor",
        "version": version,
        "built": datetime.now().isoformat(timespec="seconds"),
        "python": PY_VERSION,
        "commit": git("rev-parse", "HEAD"),
        "dirty": dirty,
        "parallax": parallax,
        "packages": installed_packages(),
    }
    (OUT / "install-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    if dirty:
        say("WARNING: this build includes changes that are not committed to git")
    return info


# Checks ---------------------------------------------------------------------------------------


def smoke_test() -> dict:
    """Start the runtime the way a team laptop will: nothing on PATH but Windows itself, the
    app's data in a throw-away folder, from another working directory."""
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
    say("smoke test: the runtime starts the app, its window library and its tools")
    result = subprocess.run(
        [str(RUNTIME / "python.exe"), "-c", SMOKE_TEST],
        env=env,
        cwd=tempfile.gettempdir(),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if result.returncode != 0 or not lines:
        output = result.stdout[-3000:] + result.stderr[-3000:]
        raise fail("the runtime smoke test failed:\n" + output)
    info = json.loads(lines[-1])
    expected = APP / "backend" / "sigma_editor" / "__init__.py"
    if Path(info["package"]).resolve() != expected.resolve():
        raise fail(f"the runtime imported the package from {info['package']}, not from app\\")
    say("smoke test passed: " + ", ".join(f"{k} {v}" for k, v in info.items() if k != "package"))
    return info


def size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1048576


def longest_path() -> tuple[int, str]:
    longest = (0, "")
    for path in OUT.rglob("*"):
        relative = str(path.relative_to(OUT))
        if "__pycache__" in relative:
            continue
        longest = max(longest, (len(relative), relative))
    return longest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build dist/9SigmaEditor (runtime and app).")
    parser.add_argument("--cache", type=Path,
                        default=Path(os.environ.get("SGE_BUILD_CACHE") or BUILD / "cache"),
                        help="where the downloaded Python zip is kept (default build/cache)")
    parallax = parser.add_mutually_exclusive_group()
    parallax.add_argument("--parallax", dest="parallax", action="store_true", default=True,
                          help="include the 2.5D parallax add-on (onnxruntime); the default")
    parallax.add_argument("--no-parallax", dest="parallax", action="store_false",
                          help="leave the parallax add-on out (scenes use the Ken Burns move)")
    parser.add_argument("--no-pin", action="store_true",
                        help="resolve the newest dependency versions instead of this venv's")
    parser.add_argument("--rebuild-frontend", action="store_true",
                        help="run npm run build even when frontend/dist looks up to date")
    parser.add_argument("--skip-frontend", action="store_true",
                        help="use frontend/dist as it is, without building")
    args = parser.parse_args(argv)

    started = time.time()
    print()
    version = read_version()
    say(f"9Sigma Editor {version}")
    unpack_runtime(fetch_embed(args.cache))
    install_packages(args.parallax, pin=not args.no_pin)
    copy_vc_runtime()
    copy_app()
    build_frontend(force=args.rebuild_frontend, skip=args.skip_frontend)
    write_launchers_and_info(version, args.parallax)
    smoke_test()
    length, where = longest_path()
    print()
    say(f"runtime: {size_mb(RUNTIME):.0f} MB   app: {size_mb(APP):.0f} MB   "
        f"total: {size_mb(OUT):.0f} MB in {OUT}")
    say(f"longest path inside the install folder: {length} characters ({where})")
    say(f"built in {time.time() - started:.0f}s. Next: tools\\make_setup_exe.py")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
