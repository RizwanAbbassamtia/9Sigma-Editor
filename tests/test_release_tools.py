"""The release tools in tools/ (docs/RELEASE-CONTRACT.md sections 1, 2 and 4): no network here.
Without GITHUB_TOKEN publishing prints the GitHub web steps and exits 2; a build of another
version, an out-of-date build or one made from uncommitted code is refused; the token is never
taken from anywhere but the environment."""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


publish = _load("publish_release")
withdraw = _load("withdraw_release")
build_runtime = _load("build_runtime")
make_setup_exe = _load("make_setup_exe")


def fake_installer(path: Path, version: str, **info: object) -> Path:
    program = b"MZ" + b"\0" * 1000
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "9SigmaEditor/install-info.json",
            json.dumps({"version": version, "commit": "abc123", "built": "2026-10-11T12:00:00",
                        **info}),
        )
    path.write_bytes(program + buffer.getvalue() + struct.pack("<Q8s", len(program), b"SIGMA9ED"))
    return path


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_publish_without_a_token_prints_the_web_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    no_token: None,
) -> None:
    asset = fake_installer(tmp_path / "Install-9SigmaEditor-0.2.0.exe", "0.2.0", dirty=False)
    monkeypatch.setattr(publish, "version", lambda: "0.2.0")
    monkeypatch.setattr(publish, "asset_path", lambda ver: asset)
    monkeypatch.setattr(publish, "SOURCES", [])

    def no_network(*args: object, **kwargs: object) -> int:
        raise AssertionError("nothing may be uploaded without a token")

    monkeypatch.setattr(publish, "publish", no_network)
    assert publish.main([]) == 2
    out = capsys.readouterr().out
    assert "No GITHUB_TOKEN is set, so nothing was uploaded" in out
    assert "https://github.com/RizwanAbbassamtia/9Sigma-Editor/releases/new" in out
    assert "v0.2.0" in out and str(asset) in out
    assert "Publish release" in out


@pytest.mark.parametrize(
    ("installer_version", "dirty", "expected"),
    [
        ("0.1.9", False, "installs version 0.1.9, but pyproject.toml says 0.2.0"),
        ("0.2.0", True, "uncommitted changes"),
    ],
)
def test_publish_refuses_a_wrong_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    no_token: None, installer_version: str, dirty: bool, expected: str,
) -> None:
    asset = fake_installer(tmp_path / "i.exe", installer_version, dirty=dirty)
    monkeypatch.setattr(publish, "version", lambda: "0.2.0")
    monkeypatch.setattr(publish, "asset_path", lambda ver: asset)
    monkeypatch.setattr(publish, "SOURCES", [])
    assert publish.main([]) == 1
    assert expected in capsys.readouterr().out


def test_publish_refuses_a_build_older_than_the_code(tmp_path: Path) -> None:
    import os

    asset = fake_installer(tmp_path / "i.exe", "0.2.0")
    code = tmp_path / "changed.py"
    code.write_text("x = 1\n", encoding="utf-8")
    os.utime(asset, (1_700_000_000, 1_700_000_000))
    problems = publish.check_build(asset, "0.2.0", [code])
    assert problems and problems[0].startswith("stale build")
    assert publish.check_build(tmp_path / "missing.exe", "0.2.0", []) == [
        f"{tmp_path / 'missing.exe'} is missing: run tools\\make_setup_exe.py first"
    ]


def test_the_token_only_comes_from_the_environment() -> None:
    for tool in ("publish_release.py", "withdraw_release.py"):
        source = (ROOT / "tools" / tool).read_text(encoding="utf-8")
        assert "credential fill" not in source
        assert 'TOKEN_ENV = "GITHUB_TOKEN"' in source


def test_withdraw_without_a_token_prints_the_web_steps(
    capsys: pytest.CaptureFixture[str], no_token: None
) -> None:
    assert withdraw.main(["v0.2.0"]) == 2
    out = capsys.readouterr().out
    assert "releases/tag/v0.2.0" in out and "nothing was changed" in out
    assert withdraw.main([]) == 2  # no version: the usage text


def test_build_refuses_two_different_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "backend" / "sigma_editor").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    init = tmp_path / "backend" / "sigma_editor" / "__init__.py"
    init.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    monkeypatch.setattr(build_runtime, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="pyproject.toml says 0.2.0 but"):
        build_runtime.read_version()
    init.write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    assert build_runtime.read_version() == "0.2.0"


def test_the_payload_leaves_out_caches_ffmpeg_and_install_leftovers() -> None:
    wanted = make_setup_exe.wanted
    assert wanted(Path("runtime/python.exe"))
    assert wanted(Path("app/backend/sigma_editor/app.py"))
    assert not wanted(Path("app/backend/sigma_editor/__pycache__/app.cpython-312.pyc"))
    assert not wanted(Path("runtime/ffmpeg/bin/ffmpeg.exe"))
    assert not wanted(Path("uninstall.exe"))
    assert not wanted(Path("install.log"))
    assert wanted(Path("runtime/Lib/site-packages/x/install.log"))


def test_the_runtime_path_file_reaches_the_app_and_its_dependencies() -> None:
    lines = build_runtime.PTH.splitlines()
    assert lines == ["python312.zip", ".", "..\\app\\backend", "Lib\\site-packages", "import site"]


def test_the_runtime_puts_the_downloaded_ffmpeg_first_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    runtime = tmp_path / "runtime"
    system = r"C:\Windows\System32"
    monkeypatch.setenv("PATH", system)
    code = compile(build_runtime.SITECUSTOMIZE, str(runtime / "sitecustomize.py"), "exec")

    exec(code, {"__file__": str(runtime / "sitecustomize.py"), "__name__": "sitecustomize"})
    assert os.environ["PATH"] == system  # nothing downloaded yet: PATH is left alone

    ffmpeg = runtime / "ffmpeg" / "bin"
    ffmpeg.mkdir(parents=True)
    (ffmpeg / "ffmpeg.exe").write_bytes(b"x")
    exec(code, {"__file__": str(runtime / "sitecustomize.py"), "__name__": "sitecustomize"})
    assert os.environ["PATH"].split(os.pathsep) == [str(ffmpeg), system]
    exec(code, {"__file__": str(runtime / "sitecustomize.py"), "__name__": "sitecustomize"})
    assert os.environ["PATH"].split(os.pathsep) == [str(ffmpeg), system]

    # A deno folder left behind by an older install is not put on PATH: only FFmpeg is.
    deno = runtime / "deno"
    deno.mkdir()
    (deno / "deno.exe").write_bytes(b"x")
    exec(code, {"__file__": str(runtime / "sitecustomize.py"), "__name__": "sitecustomize"})
    assert os.environ["PATH"].split(os.pathsep) == [str(ffmpeg), system]
