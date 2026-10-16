"""The setup program (installer/setup_app.py, docs/RELEASE-CONTRACT.md section 3) without its
window: a tiny fake payload is installed into a temporary folder, upgraded in place (FFmpeg and
anything else in the folder survive), rolled back when placing fails, and removed again. The
desktop, the Start menu and the registry are never touched."""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("setup_app", ROOT / "installer" / "setup_app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_app"] = module
    spec.loader.exec_module(module)
    return module


setup = _load()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Logs go to the test folder; nothing reaches the real shortcuts or uninstall entry."""
    monkeypatch.setattr(setup, "LOG_PATH", tmp_path / "install.log")
    monkeypatch.setattr(setup, "create_shortcuts", lambda target: pytest.fail("shortcuts"))
    monkeypatch.setattr(setup, "remove_shortcuts", lambda target: [])
    monkeypatch.setattr(setup, "write_uninstall_entry", lambda *a: pytest.fail("registry"))
    monkeypatch.setattr(setup, "remove_uninstall_entry", lambda target: False)
    # A real installer may be running on this laptop: the tests never share its name or files.
    monkeypatch.setattr(setup, "claim_instance", lambda: True)


def payload_zip(path: Path, version: str, extra: dict[str, bytes] | None = None) -> Path:
    files = {
        "VERSION": (version + "\n").encode(),
        "install-info.json": json.dumps({"version": version}).encode(),
        "9SigmaEditor.cmd": b"@echo off\r\n",
        "9SigmaEditor-Server.cmd": b"@echo off\r\n",
        "runtime/python.exe": b"MZ python " + version.encode(),
        "runtime/pythonw.exe": b"MZ pythonw",
        "runtime/Lib/site-packages/dep/__init__.py": b"",
        "app/backend/sigma_editor/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "app/frontend/dist/index.html": b"<!doctype html>",
    }
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr("9SigmaEditor/" + name, data)
    return path


def engine_for(payload: Path, target: Path, **options: Any) -> Any:
    options.setdefault("skip_ffmpeg", True)
    options.setdefault("register", False)
    options.setdefault("run_self_test", False)
    found = setup.find_payload(str(payload))
    assert found is not None
    return setup.Engine(found, target, **options)


# The payload ----------------------------------------------------------------------------------


def test_payload_appended_to_the_program_is_found(tmp_path: Path) -> None:
    zipped = payload_zip(tmp_path / "p.zip", "0.3.0").read_bytes()
    program = b"MZ" + b"\0" * 4000
    exe = tmp_path / "Install-9SigmaEditor-0.3.0.exe"
    exe.write_bytes(program + zipped + struct.pack("<Q8s", len(program), b"SIGMA9ED"))
    found = setup.find_payload(program=exe)
    assert found is not None
    assert found.offset == len(program)
    info = setup.inspect_payload(found)
    assert info.version == "0.3.0"
    assert info.files == 9
    assert info.longest == len("runtime/Lib/site-packages/dep/__init__.py")

    plain = tmp_path / "plain.exe"
    plain.write_bytes(program)
    assert setup.find_payload(program=plain) is None
    assert setup.read_trailer(plain) is None
    assert setup.find_payload(str(tmp_path / "missing.zip")) is None


# Installing -----------------------------------------------------------------------------------


def test_fresh_install(tmp_path: Path) -> None:
    target = tmp_path / "Programs" / "9SigmaEditor"
    engine = engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target)
    seen: list[str] = []
    engine.on_progress = lambda index, fraction, message: seen.append(setup.STEPS[index].key)
    engine.run_from(0)
    assert engine.version == "0.2.0"
    assert (target / "runtime" / "python.exe").read_bytes() == b"MZ python 0.2.0"
    assert json.loads((target / "install-info.json").read_text())["version"] == "0.2.0"
    assert (target / "app" / "frontend" / "dist" / "index.html").is_file()
    assert not setup.staging_for(target).exists()
    assert (target / "install.log").is_file()
    assert [key for key in dict.fromkeys(seen)] == [step.key for step in setup.STEPS]
    assert [step.key for step in setup.STEPS] == [
        "check", "extract", "install", "ffmpeg", "verify", "shortcuts"
    ]
    assert {r.name for r in engine.requirements} >= {"Windows", "Installer files", "Disk space"}


def downloaded_ffmpeg(target: Path) -> Path:
    """FFmpeg as the installer leaves it in runtime\\ (a dummy file)."""
    ffmpeg = target / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_bytes(b"ffmpeg")
    return ffmpeg


def test_upgrade_keeps_ffmpeg_and_other_files(tmp_path: Path) -> None:
    target = tmp_path / "9SigmaEditor"
    engine_for(payload_zip(tmp_path / "old.zip", "0.1.0", {"app/old_only.txt": b"x"}), target
               ).run_from(0)
    ffmpeg = downloaded_ffmpeg(target)
    (target / "notes.txt").write_text("mine", encoding="utf-8")

    upgrade = engine_for(payload_zip(tmp_path / "new.zip", "0.2.0"), target)
    assert upgrade.previous == "0.1.0"
    upgrade.run_from(0)
    assert (target / "runtime" / "python.exe").read_bytes() == b"MZ python 0.2.0"
    assert ffmpeg.read_bytes() == b"ffmpeg"  # carried over, not downloaded again
    assert (target / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (target / "app" / "old_only.txt").exists()
    assert not (target / "runtime.old").exists() and not (target / "app.old").exists()
    assert (target / "VERSION").read_text().strip() == "0.2.0"


def test_a_failed_swap_puts_the_previous_version_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "9SigmaEditor"
    engine_for(payload_zip(tmp_path / "old.zip", "0.1.0"), target).run_from(0)
    ffmpeg = downloaded_ffmpeg(target)

    upgrade = engine_for(payload_zip(tmp_path / "new.zip", "0.2.0"), target)
    original = Path.replace

    def failing_replace(self: Path, other: Any) -> Path:
        if self.name == "runtime" and self.parent == setup.staging_for(target):
            raise PermissionError("in use (simulated)")
        return original(self, other)

    upgrade.run_from(0, 1)
    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(setup.InstallError, match="Could not replace the program files"):
        upgrade.run_from(2, 2)
    monkeypatch.setattr(Path, "replace", original)
    assert (target / "runtime" / "python.exe").read_bytes() == b"MZ python 0.1.0"
    assert (target / "app" / "backend" / "sigma_editor" / "__init__.py").is_file()
    assert ffmpeg.read_bytes() == b"ffmpeg"
    # Retry works once the file is free again.
    upgrade.run_from(1)
    assert (target / "runtime" / "python.exe").read_bytes() == b"MZ python 0.2.0"
    assert ffmpeg.read_bytes() == b"ffmpeg"


def test_the_install_waits_for_the_app_and_then_says_to_close_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "9SigmaEditor"
    engine = engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target)
    engine.run_from(0, 1)
    monkeypatch.setattr(setup, "APP_CLOSE_WAIT_S", 0.2)
    monkeypatch.setattr(
        setup, "locked_runtime", lambda t: (t / "runtime" / "pythonw.exe", PermissionError("busy"))
    )
    with pytest.raises(setup.InstallError, match="9Sigma Editor is open on this computer"):
        engine.run_from(2, 2)


def test_a_damaged_payload_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 not really a zip")
    engine = setup.Engine(setup.Payload("broken", lambda: open(broken, "rb"), 10),
                          tmp_path / "t", skip_ffmpeg=True, register=False, run_self_test=False)
    with pytest.raises(setup.InstallError, match="Installer files: The installer is damaged"):
        engine.run_from(0)
    nothing = setup.Engine(None, tmp_path / "t", skip_ffmpeg=True)
    with pytest.raises(setup.InstallError, match="missing from this installer"):
        nothing.run_from(0, 0)


def test_unattended_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("SGE_INSTALL_DIR", raising=False)
    target = tmp_path / "cli"
    payload = payload_zip(tmp_path / "p.zip", "0.2.0")
    monkeypatch.setattr(setup.Engine, "step_verify", lambda self, index: None)
    code = setup.main(["--unattended", "--payload", str(payload), "--target", str(target),
                       "--skip-ffmpeg"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Installed 9Sigma Editor 0.2.0" in out
    assert "skipped (--skip-ffmpeg)" in out
    assert "deno" not in out.lower()  # nothing is downloaded for YouTube in this app
    assert (target / "install-info.json").is_file()


# FFmpeg ---------------------------------------------------------------------------------------


def tool_zip(path: Path, members: list[str]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name in members:
            archive.writestr(name, b"program " + name.encode())
    return path.as_uri()


def test_ffmpeg_is_unpacked_from_the_essentials_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = tool_zip(tmp_path / "ffmpeg.zip", [
        "ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe",
        "ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe",
        "ffmpeg-9.0.2-essentials_build/bin/ffplay.exe",
        "ffmpeg-9.0.2-essentials_build/LICENSE",
        "ffmpeg-9.0.2-essentials_build/doc/ffmpeg.html",
    ])
    monkeypatch.setattr(setup, "tool_version", lambda tool, exe: "ffmpeg version 9.0.2-test")
    bin_dir = tmp_path / "runtime" / "ffmpeg" / "bin"
    progress: list[tuple[int, int]] = []
    version = setup.install_tool(setup.FFMPEG, bin_dir,
                                 lambda done, total: progress.append((done, total)),
                                 url=url, work_dir=tmp_path)
    assert version == "ffmpeg version 9.0.2-test"
    assert sorted(p.name for p in bin_dir.iterdir()) == ["LICENSE.txt", "ffmpeg.exe", "ffprobe.exe"]
    assert progress and progress[-1][0] == progress[-1][1]


def test_the_published_checksum_is_read_and_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    zipped = tmp_path / "ffmpeg-release-essentials.zip"
    url = tool_zip(zipped, ["x/bin/ffmpeg.exe", "x/bin/ffprobe.exe"])
    digest = hashlib.sha256(zipped.read_bytes()).hexdigest()
    checksum = tmp_path / (zipped.name + ".sha256")
    checksum.write_text(f"{digest}  {zipped.name}\n", encoding="ascii")  # gyan.dev's layout
    assert setup.published_sha256(checksum.as_uri()) == digest
    assert setup.published_sha256((tmp_path / "missing.sha256").as_uri()) == ""

    monkeypatch.setattr(setup, "tool_version", lambda tool, exe: "ffmpeg version 9.0.2")
    folder = tmp_path / "runtime" / "ffmpeg" / "bin"
    assert setup.install_tool(setup.FFMPEG, folder, lambda d, t: None, url=url,
                              work_dir=tmp_path) == "ffmpeg version 9.0.2"  # checksum matched
    checksum.write_text("0" * 64 + f"  {zipped.name}\n", encoding="ascii")
    with pytest.raises(setup.ToolError, match="checksum does not match"):
        setup.install_tool(setup.FFMPEG, folder, lambda d, t: None, url=url, work_dir=tmp_path)


def test_ffmpeg_zip_without_ffprobe_is_refused(tmp_path: Path) -> None:
    url = tool_zip(tmp_path / "ffmpeg.zip", ["x/bin/ffmpeg.exe"])
    with pytest.raises(setup.ToolError, match="does not contain ffprobe.exe"):
        setup.install_tool(setup.FFMPEG, tmp_path / "bin", lambda d, t: None, url=url,
                           work_dir=tmp_path)


def test_without_internet_the_install_finishes_and_says_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "9SigmaEditor"
    engine = engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target, skip_ffmpeg=False)
    monkeypatch.setattr(setup, "tool_version", lambda tool, exe: "")

    def offline(tool: Any, folder: Path, progress: Any, url: Any = None,
                work_dir: Any = None) -> str:
        raise setup.ToolError("this computer could not reach the download site")

    monkeypatch.setattr(setup, "install_tool", offline)
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    engine.run_from(0)
    assert len(engine.warnings) == 1
    assert "needs FFmpeg to make videos" in engine.warnings[0]
    assert (target / "install-info.json").is_file()

    # With FFmpeg already on PATH, the app uses that one.
    monkeypatch.setattr(setup.shutil, "which", lambda name: rf"C:\tools\{name}.exe")
    monkeypatch.setattr(setup, "tool_version",
                        lambda tool, exe: f"{tool.key} 9" if "tools" in str(exe) else "")
    again = engine_for(payload_zip(tmp_path / "p2.zip", "0.2.1"), target, skip_ffmpeg=False)
    again.run_from(0)
    assert not again.warnings
    assert len(again.notes) == 1
    assert r"C:\tools\ffmpeg.exe" in again.notes[0]


def test_a_working_download_is_kept_and_not_fetched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "9SigmaEditor"
    engine = engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target, skip_ffmpeg=False)
    engine.run_from(0, 2)
    downloaded_ffmpeg(target)
    (target / "runtime" / "ffmpeg" / "bin" / "ffprobe.exe").write_bytes(b"ffprobe")
    monkeypatch.setattr(setup, "tool_version", lambda tool, exe: f"{tool.key} works")
    monkeypatch.setattr(setup, "install_tool",
                        lambda *a, **k: pytest.fail("nothing may be downloaded again"))
    seen: list[str] = []
    engine.on_progress = lambda index, fraction, message: seen.append(message)
    engine.run_from(3, 3)
    assert "already installed: ffmpeg works" in seen


# Uninstall and helpers ------------------------------------------------------------------------


def test_uninstall_removes_the_program_and_keeps_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "9SigmaEditor"
    engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target).run_from(0)
    ok, message = setup.uninstall(target, wait_seconds=1)
    assert ok, message
    assert not target.exists()
    assert "projects, exports and settings are kept" in message


def test_uninstall_waits_for_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "9SigmaEditor"
    engine_for(payload_zip(tmp_path / "p.zip", "0.2.0"), target).run_from(0)
    monkeypatch.setattr(setup, "locked_runtime", lambda t: (t, PermissionError("busy")))
    ok, message = setup.uninstall(target, wait_seconds=0.2)
    assert not ok and "still open" in message
    assert target.exists()


def test_the_app_starts_without_the_setup_program_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("_PYI_ARCHIVE_FILE", "_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"):
        monkeypatch.setenv(name, "x")
    env = setup.clean_environment()
    assert not {"_PYI_ARCHIVE_FILE", "_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"} & set(env)
    assert "PATH" in env or "Path" in env


def test_the_install_folder_can_be_chosen_with_sge_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SGE_INSTALL_DIR", str(tmp_path / "Apps" / "9SigmaEditor"))
    assert setup.default_target() == tmp_path / "Apps" / "9SigmaEditor"
    monkeypatch.delenv("SGE_INSTALL_DIR")
    assert setup.default_target().parts[-2:] == ("Programs", "9SigmaEditor")


def test_long_install_folders_are_refused(tmp_path: Path) -> None:
    deep = tmp_path / ("x" * 120) / ("y" * 100)
    payload = payload_zip(tmp_path / "p.zip", "0.2.0")
    found = setup.find_payload(str(payload))
    requirements, _ = setup.check_requirements(found, deep, skip_ffmpeg=True)
    failing = [r for r in requirements if r.status == "fail"]
    assert failing and failing[0].name == "Folder name length"


def test_payload_names_cannot_escape_the_install_folder(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("9SigmaEditor/install-info.json", json.dumps({"version": "0.2.0"}))
        archive.writestr("9SigmaEditor/../evil.txt", b"x")
    path = tmp_path / "evil.zip"
    path.write_bytes(buffer.getvalue())
    engine = engine_for(path, tmp_path / "9SigmaEditor")
    engine.run_from(0, 0)
    with pytest.raises(setup.InstallError, match="outside the program folder"):
        engine.run_from(1, 1)
    assert not (tmp_path / "evil.txt").exists()
