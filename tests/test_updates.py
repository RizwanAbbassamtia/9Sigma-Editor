"""In-app updates (docs/RELEASE-CONTRACT.md sections 1, 4 and 5): version compare, reading a
GitHub release, the background check, the verified download and the API. No test touches the
network: every HTTP call goes to an ``httpx.MockTransport`` with a fake release JSON."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sigma_editor import updates
from sigma_editor.updates import (
    API_LATEST,
    BUSY_JOB,
    BUSY_VIDEO,
    DOWNLOAD_PREFIX,
    NOT_INSTALLED_BLOCKER,
    WEB_LATEST,
    Release,
    UpdateCheckError,
    UpdateInstallError,
    UpdateManager,
    compare_versions,
    embedded_version,
    fetch_latest_release,
    install_root,
    is_newer,
    parse_version,
    release_from_api,
    release_from_redirect,
    verify_installer,
)

REPO_PAGE = "https://github.com/RizwanAbbassamtia/9Sigma-Editor"


# Fakes ----------------------------------------------------------------------------------------


def installer_bytes(version: str, *, magic: bytes = b"SIGMA9ED", head: bytes = b"MZ") -> bytes:
    """A tiny file shaped like the Setup EXE: a program, the payload zip, the trailer."""
    program = head + b"\0" * 2046
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("9SigmaEditor/install-info.json", json.dumps({"version": version}))
        archive.writestr("9SigmaEditor/VERSION", version + "\n")
    return program + buffer.getvalue() + struct.pack("<Q8s", len(program), magic)


def release_json(version: str = "0.2.0", data: bytes | None = None, **extra: Any) -> dict[str, Any]:
    """The GitHub API's release object, as ``/releases/latest`` returns it."""
    data = installer_bytes(version) if data is None else data
    name = f"Install-9SigmaEditor-{version}.exe"
    body: dict[str, Any] = {
        "tag_name": f"v{version}",
        "name": f"9Sigma Editor {version}",
        "html_url": f"{REPO_PAGE}/releases/tag/v{version}",
        "body": "Faster renders.\n\nFixes the sign-in page.",
        "published_at": "2026-10-11T10:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "notes.txt",
                "size": 10,
                "browser_download_url": f"{DOWNLOAD_PREFIX}v{version}/notes.txt",
            },
            {
                "name": name,
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "browser_download_url": f"{DOWNLOAD_PREFIX}v{version}/{name}",
            },
        ],
    }
    body.update(extra)
    return body


Handler = Callable[[httpx.Request], httpx.Response]


def github(
    *,
    api: httpx.Response | Exception | None = None,
    web: httpx.Response | Exception | None = None,
    files: dict[str, bytes] | None = None,
    calls: list[str] | None = None,
) -> Handler:
    """A fake github.com: the API answer, the release page answer and downloadable files
    (each download first redirects to a storage host, as GitHub does)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        if url == API_LATEST:
            if isinstance(api, Exception):
                raise api
            return api or httpx.Response(404, json={"message": "Not Found"})
        if url == WEB_LATEST:
            if isinstance(web, Exception):
                raise web
            return web or httpx.Response(302, headers={"location": f"{REPO_PAGE}/releases"})
        if url.startswith(DOWNLOAD_PREFIX):
            name = url.rsplit("/", 1)[-1]
            return httpx.Response(302, headers={"location": f"https://objects.example.test/{name}"})
        if url.startswith("https://objects.example.test/"):
            name = url.rsplit("/", 1)[-1]
            if files and name in files:
                return httpx.Response(
                    200, content=files[name], headers={"content-type": "application/octet-stream"}
                )
            return httpx.Response(404)
        return httpx.Response(500)

    return handler


def factory(handler: Handler) -> Callable[[], httpx.Client]:
    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def run_now(_name: str, work: Callable[[], None]) -> None:
    work()


def offline() -> httpx.Response:
    raise httpx.ConnectError("getaddrinfo failed")


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def installed_root(tmp_path: Path) -> Path:
    root = tmp_path / "inst"
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / "python.exe").write_bytes(b"MZ")
    (root / "install-info.json").write_text('{"version": "0.1.0"}', encoding="utf-8")
    return root


def manager(handler: Handler, *, root: Path | None = None, **options: Any) -> UpdateManager:
    options.setdefault("current_version", "0.1.0")
    options.setdefault("runner", run_now)
    options.setdefault("windows", True)
    return UpdateManager(root=root, client_factory=factory(handler), **options)


# Versions -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0.2.0", "0.1.0", 1),
        ("0.1.0", "0.2.0", -1),
        ("v0.2.0", "0.2.0", 0),
        ("V1.2", "1.2.0", 0),
        ("0.10.0", "0.9.0", 1),
        ("0.9.9", "0.10.0", -1),
        ("1.0", "1.0.0", 0),
        ("2", "1.9.9", 1),
        ("1.0.0rc1", "1.0.0", -1),
        ("1.0.0", "1.0.0-beta.2", 1),
        ("1.0.0rc10", "1.0.0rc9", 1),
        ("1.0.0-beta.2", "1.0.0-beta.10", -1),
        ("0.1.0+build.7", "0.1.0", 0),
        ("garbage", "0.1.0", None),
        ("", "0.1.0", None),
        ("0.1.0", None, None),
    ],
)
def test_compare_versions(a: str, b: str | None, expected: int | None) -> None:
    assert compare_versions(a, b) == expected


def test_is_newer_never_offers_a_non_version() -> None:
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("latest", "0.1.0")
    assert not is_newer("0.1.0rc1", "0.1.0")
    assert parse_version("v3.02.1") == ((3, 2, 1), "")


def test_running_version_matches_the_one_the_build_reads() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    from sigma_editor import __version__

    assert __version__ == declared, "bump pyproject.toml and sigma_editor/__init__.py together"


# Reading a release ----------------------------------------------------------------------------


def test_release_from_api_reads_the_installer_asset() -> None:
    data = installer_bytes("0.2.0")
    release = release_from_api(release_json("0.2.0", data))
    assert release.version == "0.2.0"
    assert release.tag == "v0.2.0"
    assert release.html_url == f"{REPO_PAGE}/releases/tag/v0.2.0"
    assert release.installer_name == "Install-9SigmaEditor-0.2.0.exe"
    assert release.installer_url == f"{DOWNLOAD_PREFIX}v0.2.0/Install-9SigmaEditor-0.2.0.exe"
    assert release.installer_size == len(data)
    assert release.installer_sha256 == hashlib.sha256(data).hexdigest()
    assert release.notes.startswith("Faster renders.")
    assert release.published_at == "2026-10-11T10:00:00Z"


def test_release_from_api_ignores_installers_hosted_elsewhere() -> None:
    raw = release_json("0.2.0")
    raw["assets"][1]["browser_download_url"] = (
        "https://evil.example/Install-9SigmaEditor-0.2.0.exe"
    )
    release = release_from_api(raw)
    assert release.installer_url == ""
    assert release.installer_name == ""


def test_release_from_api_refuses_a_tag_that_is_not_a_version() -> None:
    with pytest.raises(ValueError):
        release_from_api(release_json("0.2.0", tag_name="nightly"))
    with pytest.raises(ValueError):
        release_from_api(["not", "a", "release"])


def test_release_from_redirect() -> None:
    release = release_from_redirect(f"{REPO_PAGE}/releases/tag/v0.3.1")
    assert release is not None
    assert release.version == "0.3.1"
    assert release.installer_url == f"{DOWNLOAD_PREFIX}v0.3.1/Install-9SigmaEditor-0.3.1.exe"
    assert release.installer_size == 0 and release.installer_sha256 == ""
    assert release_from_redirect(f"{REPO_PAGE}/releases") is None


def test_fetch_uses_the_api_first() -> None:
    calls: list[str] = []
    handler = github(api=httpx.Response(200, json=release_json("0.2.0")), calls=calls)
    with factory(handler)() as client:
        release = fetch_latest_release(client)
    assert release is not None and release.version == "0.2.0"
    assert calls == [API_LATEST]


def test_fetch_falls_back_to_the_release_page_when_the_api_is_rationed() -> None:
    handler = github(
        api=httpx.Response(403, json={"message": "API rate limit exceeded"}),
        web=httpx.Response(302, headers={"location": f"{REPO_PAGE}/releases/tag/v0.4.0"}),
    )
    with factory(handler)() as client:
        release = fetch_latest_release(client)
    assert release is not None and release.version == "0.4.0"


def test_fetch_knows_when_nothing_is_published_yet() -> None:
    with factory(github())() as client:
        assert fetch_latest_release(client) is None


def test_fetch_offline_says_so_in_plain_english() -> None:
    offline = httpx.ConnectError("getaddrinfo failed")
    with (
        factory(github(api=offline, web=offline))() as client,
        pytest.raises(UpdateCheckError, match="could not reach GitHub"),
    ):
        fetch_latest_release(client)


# The background check -------------------------------------------------------------------------


def test_check_finds_a_newer_version() -> None:
    updater = manager(github(api=httpx.Response(200, json=release_json("0.2.0"))))
    assert updater.status().status == "unknown"
    assert updater.maybe_check() is True
    status = updater.status()
    assert status.status == "available"
    assert status.latest_version == "0.2.0"
    assert status.message == "Version 0.2.0 is available. This laptop has 0.1.0."
    assert status.release_url == f"{REPO_PAGE}/releases/tag/v0.2.0"
    assert status.installer is not None and status.installer.name.endswith("0.2.0.exe")
    # A developer's checkout cannot install it, and says why.
    assert status.can_install is False
    assert status.install_blocker == NOT_INSTALLED_BLOCKER
    assert status.checked_at is not None


def test_check_up_to_date_and_nothing_published() -> None:
    same = manager(github(api=httpx.Response(200, json=release_json("0.1.0"))))
    same.maybe_check()
    assert same.status().status == "up_to_date"
    assert same.status().message == "9Sigma Editor is up to date (version 0.1.0)."
    assert same.status().can_install is False

    empty = manager(github())
    empty.maybe_check()
    assert empty.status().status == "up_to_date"
    assert empty.status().message == "No update has been published yet."


def test_checks_are_spaced_six_hours_apart_and_retried_sooner_after_a_failure() -> None:
    clock = Clock()
    answers: list[Callable[[], httpx.Response]] = [offline]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers[0]()

    updater = manager(handler, clock=clock)
    assert updater.maybe_check() is True
    failed = updater.status()
    assert failed.status == "check_failed"
    assert failed.message.startswith("Could not check for updates: this computer could not reach")
    clock.now += 29 * 60
    assert updater.maybe_check() is False
    clock.now += 2 * 60
    answers[0] = lambda: httpx.Response(200, json=release_json("0.1.0"))
    assert updater.maybe_check() is True
    assert updater.status().status == "up_to_date"
    clock.now += 5 * 3600
    assert updater.maybe_check() is False
    clock.now += 3600 + 1
    assert updater.maybe_check() is True
    # "Try again" checks at once whatever the timer says.
    assert updater.check_now() is True


def test_a_failed_recheck_keeps_offering_the_version_found_earlier() -> None:
    answers: list[Callable[[], httpx.Response]] = [
        lambda: httpx.Response(200, json=release_json("0.2.0"))
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers[0]()

    updater = manager(handler)
    updater.check_now()
    answers[0] = offline
    updater.check_now()
    assert updater.status().status == "available"


# Installing -----------------------------------------------------------------------------------


def test_update_now_downloads_verifies_starts_the_installer_and_closes_the_app(
    tmp_path: Path, installed_root: Path
) -> None:
    data = installer_bytes("0.2.0")
    started: list[tuple[Path, Path]] = []
    closed: list[bool] = []
    updater = manager(
        github(
            api=httpx.Response(200, json=release_json("0.2.0", data)),
            files={"Install-9SigmaEditor-0.2.0.exe": data},
        ),
        root=installed_root,
        launcher=lambda installer, target: started.append((installer, target)),
        exiter=lambda: closed.append(True),
        download_dir=tmp_path / "dl",
    )
    updater.check_now()
    ready = updater.status()
    assert ready.can_install is True and ready.install_blocker == ""

    updater.start_install()
    status = updater.status()
    assert status.status == "installing"
    assert "opens again by itself" in status.message
    assert closed == [True]
    assert len(started) == 1
    installer, target = started[0]
    assert target == installed_root
    assert installer == tmp_path / "dl" / "Install-9SigmaEditor-0.2.0.exe"
    assert installer.read_bytes() == data
    assert not (tmp_path / "dl" / "Install-9SigmaEditor-0.2.0.exe.part").exists()


def test_a_verified_installer_already_downloaded_is_not_fetched_again(
    tmp_path: Path, installed_root: Path
) -> None:
    data = installer_bytes("0.2.0")
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "Install-9SigmaEditor-0.2.0.exe").write_bytes(data)
    calls: list[str] = []
    updater = manager(
        github(api=httpx.Response(200, json=release_json("0.2.0", data)), calls=calls),
        root=installed_root,
        launcher=lambda installer, target: None,
        exiter=lambda: None,
        download_dir=folder,
    )
    updater.check_now()
    updater.start_install()
    assert updater.status().status == "installing"
    assert not any("objects.example.test" in url for url in calls)


@pytest.mark.parametrize(
    ("served", "expected"),
    [
        (installer_bytes("0.1.0"), "does not match the checksum"),
        (b"not an installer at all, just text", "bytes but the release says"),
    ],
)
def test_a_wrong_download_is_refused(
    tmp_path: Path, installed_root: Path, served: bytes, expected: str
) -> None:
    published = installer_bytes("0.2.0")
    started: list[Any] = []
    updater = manager(
        github(
            api=httpx.Response(200, json=release_json("0.2.0", published)),
            files={"Install-9SigmaEditor-0.2.0.exe": served},
        ),
        root=installed_root,
        launcher=lambda installer, target: started.append(installer),
        exiter=lambda: None,
        download_dir=tmp_path / "dl",
    )
    updater.check_now()
    updater.start_install()
    status = updater.status()
    assert status.status == "install_failed"
    assert expected in status.message
    assert status.message.startswith("The update could not be installed:")
    assert started == []
    assert list((tmp_path / "dl").iterdir()) == []


def test_without_a_digest_the_version_inside_the_installer_is_still_checked(
    tmp_path: Path, installed_root: Path
) -> None:
    # The fallback (release page redirect) knows no size or digest: the payload must still
    # name the version, or the same update would be offered again after every install.
    old_inside = installer_bytes("0.1.0")
    updater = manager(
        github(
            api=httpx.Response(403),
            web=httpx.Response(302, headers={"location": f"{REPO_PAGE}/releases/tag/v0.2.0"}),
            files={"Install-9SigmaEditor-0.2.0.exe": old_inside},
        ),
        root=installed_root,
        launcher=lambda installer, target: None,
        exiter=lambda: None,
        download_dir=tmp_path / "dl",
    )
    updater.check_now()
    updater.start_install()
    status = updater.status()
    assert status.status == "install_failed"
    assert "contains version 0.1.0, not 0.2.0" in status.message


def test_update_now_waits_while_a_video_is_being_made(installed_root: Path) -> None:
    updater = manager(
        github(api=httpx.Response(200, json=release_json("0.2.0"))),
        root=installed_root,
        busy=lambda: BUSY_VIDEO,
    )
    updater.check_now()
    status = updater.status()
    assert status.can_install is False and status.install_blocker == BUSY_VIDEO
    with pytest.raises(UpdateInstallError, match="video is being made"):
        updater.start_install()


def test_update_now_with_nothing_newer_is_refused(installed_root: Path) -> None:
    same = github(api=httpx.Response(200, json=release_json("0.1.0")))
    updater = manager(same, root=installed_root)
    updater.check_now()
    with pytest.raises(UpdateInstallError, match="no newer version"):
        updater.start_install()


def test_verify_installer_and_embedded_version(tmp_path: Path) -> None:
    good = tmp_path / "good.exe"
    good.write_bytes(installer_bytes("0.2.0"))
    assert embedded_version(good) == "0.2.0"
    release = Release(version="0.2.0", tag="v0.2.0", html_url="x")
    assert verify_installer(good, release) == ""

    no_trailer = tmp_path / "plain.exe"
    no_trailer.write_bytes(b"MZ" + b"\0" * 100)
    assert embedded_version(no_trailer) is None
    assert "not a 9Sigma Editor installer" in verify_installer(no_trailer, release)

    not_exe = tmp_path / "text.exe"
    not_exe.write_bytes(installer_bytes("0.2.0", head=b"<!"))
    assert "not a Windows program" in verify_installer(not_exe, release)

    sized = Release(version="0.2.0", tag="v0.2.0", html_url="x", installer_size=5)
    assert "bytes but the release says" in verify_installer(good, sized)


def test_install_root_only_for_an_installed_layout(tmp_path: Path) -> None:
    root = tmp_path / "9SigmaEditor"
    module = root / "app" / "backend" / "sigma_editor" / "updates.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    assert install_root(module) is None  # no runtime, no install-info.json yet
    (root / "runtime").mkdir()
    (root / "runtime" / "python.exe").write_bytes(b"MZ")
    (root / "install-info.json").write_text("{}", encoding="utf-8")
    assert install_root(module) == root.resolve()
    # The repository itself (backend/sigma_editor/updates.py) is not an installed copy.
    assert install_root(updates.__file__) is None


# API ------------------------------------------------------------------------------------------


def use_manager(client: TestClient, updater: UpdateManager) -> UpdateManager:
    client.app.state.update_manager = updater  # type: ignore[attr-defined]
    return updater


def test_api_get_reports_the_check(client: TestClient) -> None:
    use_manager(client, manager(github(api=httpx.Response(200, json=release_json("0.2.0")))))
    response = client.get("/api/system/update")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert body["current_version"] == "0.1.0"
    assert body["latest_version"] == "0.2.0"
    assert body["can_install"] is False
    assert body["install_blocker"] == NOT_INSTALLED_BLOCKER
    assert body["progress"] is None
    assert set(body) == {
        "current_version",
        "status",
        "checking",
        "message",
        "latest_version",
        "release_url",
        "release_notes",
        "published_at",
        "installer",
        "can_install",
        "install_blocker",
        "checked_at",
        "progress",
    }


def test_api_install_refused_with_a_plain_reason(client: TestClient) -> None:
    use_manager(client, manager(github(api=httpx.Response(200, json=release_json("0.2.0")))))
    client.post("/api/system/update/check")
    response = client.post("/api/system/update/install")
    assert response.status_code == 409
    assert response.json()["detail"] == NOT_INSTALLED_BLOCKER


def test_api_install_starts_the_update(
    client: TestClient, tmp_path: Path, installed_root: Path
) -> None:
    data = installer_bytes("0.2.0")
    started: list[Any] = []
    use_manager(
        client,
        manager(
            github(
                api=httpx.Response(200, json=release_json("0.2.0", data)),
                files={"Install-9SigmaEditor-0.2.0.exe": data},
            ),
            root=installed_root,
            launcher=lambda installer, target: started.append((installer, target)),
            exiter=lambda: None,
            download_dir=tmp_path / "dl",
        ),
    )
    assert client.post("/api/system/update/check").status_code == 202
    response = client.post("/api/system/update/install")
    assert response.status_code == 202
    assert client.get("/api/system/update").json()["status"] == "installing"
    assert started and started[0][1] == installed_root


def test_api_needs_a_session(client: TestClient) -> None:
    """Any signed-in account may check for updates (every account is equal here); without a
    session the routes answer 401."""
    use_manager(client, manager(github()))
    assert client.get("/api/system/update").status_code == 200
    assert client.post("/api/system/update/check").status_code == 202
    client.cookies.clear()
    assert client.get("/api/system/update").status_code == 401


def test_busy_reason_reads_the_render_runner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sigma_editor.api.updates import busy_reason
    from sigma_editor.edit.render_jobs import RenderRunner

    app: Any = client.app
    jobs = app.state.jobs
    assert isinstance(jobs, RenderRunner)
    assert busy_reason(app) == ""

    # A video is being made: the runner says it is busy.
    monkeypatch.setattr(jobs, "busy", lambda: True)
    assert busy_reason(app) == BUSY_VIDEO
    monkeypatch.setattr(jobs, "busy", lambda: False)

    # Another job is still active in the registry.
    job = jobs.registry.create("render")
    jobs.registry.update(job.id, status="running")
    assert busy_reason(app) == BUSY_JOB
    jobs.registry.finish(job.id)
    assert busy_reason(app) == ""
