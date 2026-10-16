"""Build dist/Install-9SigmaEditor-<version>.exe: the one file a team member double-clicks.

    .venv\\Scripts\\python.exe tools\\make_setup_exe.py
    .venv\\Scripts\\python.exe tools\\make_setup_exe.py --cache F:\\somewhere\\cache

Three stages (the approach of the owner's earlier app, SceneForge), each reused when its output
is already there and up to date:

  1. dist/9SigmaEditor            the app with its own Python (tools/build_runtime.py, run
                                   first when the folder is missing, carries another version,
                                   or is older than the code)
  2. build/9SigmaEditor-<version>-payload.zip
                                   that folder zipped, without compiled-Python caches
  3. dist/Install-9SigmaEditor-<version>.exe
                                   installer/setup_app.py frozen with PyInstaller (one file, a
                                   small tkinter window), the payload appended after it and a
                                   16-byte trailer saying where the payload starts
                                   (docs/RELEASE-CONTRACT.md section 3)

Appending rather than bundling is what lets the window open at once: PyInstaller unpacks only
the ~10 MB setup program, which then streams the payload from its own file into the install
folder with a real progress bar. Bundling the payload inside would mean an invisible unpack of
the whole app before any window appears.

PyInstaller is installed into its own environment (build/setup-tools), so the development
environment is never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_DIR = DIST / "9SigmaEditor"
ICON = ROOT / "assets" / "icons" / "sigma.ico"
SETUP_SOURCE = ROOT / "installer" / "setup_app.py"
TOOLS_ENV = BUILD / "setup-tools"
PYINSTALLER = "pyinstaller==6.22.3"
MAGIC = b"SIGMA9ED"
TRAILER = struct.Struct("<Q8s")

SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_TOP = {"install.log", "uninstall.exe"}
STORE_SUFFIXES = {".zip", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".gz", ".woff2", ".whl"}
"""Already compressed: deflating them again costs time and saves nothing."""
SOURCES = [
    ROOT / "backend" / "sigma_editor",
    ROOT / "frontend" / "src",
    ROOT / "config",
    ROOT / "pyproject.toml",
    ROOT / "tools" / "build_runtime.py",  # it writes runtime files (sitecustomize.py)
]

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '9Sigma Editor'),
      StringStruct('FileDescription', '9Sigma Editor Setup'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'setup_app'),
      StringStruct('OriginalFilename', '{name}'),
      StringStruct('ProductName', '9Sigma Editor'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def say(text: str) -> None:
    print("  " + text, flush=True)


def fail(text: str) -> SystemExit:
    return SystemExit("\n  BUILD STOPPED: " + text + "\n")


def version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
               ["version"])


def installer_name(ver: str) -> str:
    return f"Install-9SigmaEditor-{ver}.exe"


def newest_source() -> tuple[float, Path | None]:
    newest, where = 0.0, None
    for base in SOURCES:
        paths = [base] if base.is_file() else base.rglob("*")
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts:
                stamp = path.stat().st_mtime
                if stamp > newest:
                    newest, where = stamp, path
    return newest, where


def app_dir_problem(ver: str) -> str:
    """Why dist/9SigmaEditor cannot be used as it is ("" when it can)."""
    info_path = APP_DIR / "install-info.json"
    if not info_path.is_file() or not (APP_DIR / "runtime" / "python.exe").is_file():
        return "dist/9SigmaEditor has not been built yet"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if str(info.get("version")) != ver:
        return f"dist/9SigmaEditor is version {info.get('version')}, pyproject.toml says {ver}"
    stamp, where = newest_source()
    if where is not None and info_path.stat().st_mtime < stamp:
        return f"{where.relative_to(ROOT).as_posix()} changed after dist/9SigmaEditor was built"
    return ""


def ensure_app_dir(ver: str, cache: str | None, reuse: bool) -> None:
    problem = app_dir_problem(ver)
    if not problem:
        say("dist/9SigmaEditor is up to date")
        return
    if reuse and (APP_DIR / "install-info.json").is_file():
        say(f"WARNING: {problem}; using it anyway (--reuse-runtime)")
        return
    say(f"{problem}: running tools/build_runtime.py first")
    command = [sys.executable, str(ROOT / "tools" / "build_runtime.py")]
    if cache:
        command += ["--cache", cache]
    subprocess.run(command, check=True)
    problem = app_dir_problem(ver)
    if problem:
        raise fail(problem)


def wanted(relative: Path) -> bool:
    if any(part in SKIP_DIRS for part in relative.parts):
        return False
    if relative.suffix.lower() in SKIP_SUFFIXES:
        return False
    if len(relative.parts) == 1 and relative.name in SKIP_TOP:
        return False
    # FFmpeg is downloaded by the installer; a copy put into dist by hand is not shipped.
    return relative.parts[:2] != ("runtime", "ffmpeg")


def newest_in(folder: Path) -> float:
    return max((p.stat().st_mtime for p in folder.rglob("*")
                if p.is_file() and wanted(p.relative_to(folder))), default=0.0)


def build_payload(ver: str) -> Path:
    BUILD.mkdir(exist_ok=True)
    payload = BUILD / f"9SigmaEditor-{ver}-payload.zip"
    if payload.exists() and payload.stat().st_mtime > newest_in(APP_DIR):
        say(f"the payload is up to date ({payload.stat().st_size / 1048576:.0f} MB)")
        return payload
    payload.unlink(missing_ok=True)
    say("packing the payload (the slow part: the runtime is large)")
    started = time.time()
    count = raw = 0
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(APP_DIR.rglob("*")):
            relative = path.relative_to(APP_DIR)
            if not path.is_file() or not wanted(relative):
                continue
            method = (zipfile.ZIP_STORED if path.suffix.lower() in STORE_SUFFIXES
                      else zipfile.ZIP_DEFLATED)
            archive.write(path, "9SigmaEditor/" + relative.as_posix(), compress_type=method)
            raw += path.stat().st_size
            count += 1
    say(f"payload: {count} files, {raw / 1048576:.0f} MB -> {payload.stat().st_size / 1048576:.0f} "
        f"MB zipped, in {time.time() - started:.0f}s")
    return payload


def tools_python() -> Path:
    """build/setup-tools: a virtual environment with PyInstaller (and tkinter from the base
    Python), made on first use."""
    python = TOOLS_ENV / "Scripts" / "python.exe"
    if not python.exists():
        say(f"making {TOOLS_ENV.relative_to(ROOT)} for PyInstaller")
        subprocess.run([sys.executable, "-m", "venv", str(TOOLS_ENV)], check=True)
    probe = subprocess.run(
        [str(python), "-c", "import PyInstaller, tkinter; print(PyInstaller.__version__)"],
        capture_output=True, text=True, check=False,
    )
    expected = PYINSTALLER.split("==", 1)[1]
    if probe.returncode != 0 or probe.stdout.strip() != expected:
        say(f"installing {PYINSTALLER} into {TOOLS_ENV.relative_to(ROOT)}")
        subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", PYINSTALLER], check=True)
        probe = subprocess.run(
            [str(python), "-c", "import PyInstaller, tkinter; print(PyInstaller.__version__)"],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode != 0:
            raise fail("PyInstaller or tkinter is not usable in build/setup-tools:\n"
                       + probe.stderr[-1500:])
    return python


def freeze(ver: str) -> Path:
    work = BUILD / "pyinstaller"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    name = installer_name(ver)
    numbers = tuple(int(part) for part in (ver.split("+")[0].split(".") + ["0"] * 4)[:4]
                    if part.isdigit()) or (0, 0, 0, 0)
    numbers = (numbers + (0, 0, 0, 0))[:4]
    version_file = work / "version-info.txt"
    version_file.write_text(VERSION_INFO.format(numbers=numbers, version=ver, name=name),
                            encoding="utf-8")
    python = tools_python()
    say("freezing installer/setup_app.py with PyInstaller")
    started = time.time()
    command = [
        str(python), "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed", "--log-level", "WARN",
        "--name", Path(name).stem,
        "--icon", str(ICON),
        "--add-data", f"{ICON}{os.pathsep}.",
        "--version-file", str(version_file),
        "--distpath", str(work / "dist"),
        "--workpath", str(work / "work"),
        "--specpath", str(work),
        str(SETUP_SOURCE),
    ]
    subprocess.run(command, check=True)
    exe = work / "dist" / name
    if not exe.exists():
        raise fail("PyInstaller produced no program")
    say(f"setup program: {exe.stat().st_size / 1048576:.1f} MB in {time.time() - started:.0f}s")
    return exe


def append_payload(exe: Path, payload: Path, output: Path) -> None:
    DIST.mkdir(exist_ok=True)
    output.unlink(missing_ok=True)
    shutil.copy2(exe, output)
    offset = output.stat().st_size
    with open(output, "ab") as out, open(payload, "rb") as source:
        shutil.copyfileobj(source, out, 1024 * 1024)
        out.write(TRAILER.pack(offset, MAGIC))
    say(f"payload appended at byte {offset}")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the 9Sigma Editor Setup EXE.")
    parser.add_argument("--cache", help="passed to tools/build_runtime.py when it has to run")
    parser.add_argument("--reuse-runtime", action="store_true",
                        help="use dist/9SigmaEditor even when it looks older than the code")
    args = parser.parse_args(argv)

    started = time.time()
    ver = version()
    output = DIST / installer_name(ver)
    print()
    say(f"9Sigma Editor {ver} -> {output.relative_to(ROOT)}")
    ensure_app_dir(ver, args.cache, args.reuse_runtime)
    payload = build_payload(ver)
    exe = freeze(ver)
    append_payload(exe, payload, output)
    size = output.stat().st_size
    print()
    say(f"wrote {output}")
    say(f"size {size / 1048576:.0f} MB ({size} bytes), sha256 {sha256_of(output)}")
    say(f"built {datetime.now():%Y-%m-%d %H:%M} in {time.time() - started:.0f}s")
    say(f"Try it:   {output.name} --target C:\\Temp\\sge-test --unattended --skip-ffmpeg")
    say("Publish:  tools\\publish_release.py (see docs/INSTALL.md)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
