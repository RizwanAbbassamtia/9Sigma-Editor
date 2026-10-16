"""Publish the built installer as a GitHub release, so every installed app offers it.

    set GITHUB_TOKEN=<token>
    .venv\\Scripts\\python.exe tools\\publish_release.py "What changed, in a line or two"

Creates release ``v<version>`` in RizwanAbbassamtia/9Sigma-Editor with
``dist/Install-9SigmaEditor-<version>.exe`` attached (docs/RELEASE-CONTRACT.md section 4), then
reads the latest release back the way the app does. The installed apps notice it at their next
check: when they start, and every 6 hours while open.

* The token is read from the ``GITHUB_TOKEN`` environment variable and nowhere else (never from
  git's credential store), is only sent to api.github.com and uploads.github.com, and is never
  printed. A fine-grained token for this one repository with "Contents: Read and write" is
  enough.
* Without ``GITHUB_TOKEN`` nothing is uploaded: the steps for the GitHub web page are printed
  instead and the exit code is 2.
* Before anything is uploaded the build is checked (exit code 1 when it is refused): the
  installer must exist, carry the version in pyproject.toml, be newer than the last change to
  the code, and be built from committed code (``--allow-dirty`` overrides the last one). An
  installer that would install another version than its tag would be offered for ever.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPO = "RizwanAbbassamtia/9Sigma-Editor"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TOKEN_ENV = "GITHUB_TOKEN"
MAGIC = b"SIGMA9ED"
TRAILER = struct.Struct("<Q8s")
INFO_NAME = "9SigmaEditor/install-info.json"
SOURCES = [
    ROOT / "backend" / "sigma_editor",
    ROOT / "frontend" / "src",
    ROOT / "config",
    ROOT / "pyproject.toml",
    ROOT / "tools" / "build_runtime.py",
    ROOT / "installer" / "setup_app.py",
]


def say(text: str = "") -> None:
    print(("  " + text) if text else "", flush=True)


def version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
               ["version"])


def asset_path(ver: str) -> Path:
    return ROOT / "dist" / f"Install-9SigmaEditor-{ver}.exe"


# Build checks --------------------------------------------------------------------------------


class _Slice:
    """A file object over one region of the installer, so zipfile reads the appended payload
    where it is."""

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


def embedded_info(asset: Path) -> dict[str, Any]:
    """install-info.json from the payload appended to the installer; {} when unreadable."""
    try:
        total = asset.stat().st_size
        with open(asset, "rb") as handle:
            handle.seek(total - TRAILER.size)
            offset, magic = TRAILER.unpack(handle.read(TRAILER.size))
        if magic != MAGIC or not 0 < offset < total - TRAILER.size:
            return {}
        part = _Slice(asset, offset, total - TRAILER.size - offset)
        try:
            with zipfile.ZipFile(part) as archive:  # type: ignore[arg-type]
                info = json.loads(archive.read(INFO_NAME).decode("utf-8"))
        finally:
            part.close()
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, struct.error):
        return {}


def newest_source(sources: list[Path]) -> tuple[float, Path | None]:
    newest, where = 0.0, None
    for base in sources:
        paths = [base] if base.is_file() else base.rglob("*")
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts:
                stamp = path.stat().st_mtime
                if stamp > newest:
                    newest, where = stamp, path
    return newest, where


def check_build(asset: Path, ver: str, sources: list[Path], allow_dirty: bool = False) -> list[str]:
    """Reasons the installer must not be published. Empty means go ahead."""
    if not asset.exists():
        return [f"{asset} is missing: run tools\\make_setup_exe.py first"]
    problems = []
    info = embedded_info(asset)
    if not info:
        problems.append(f"{asset.name} is not an installer made by tools/make_setup_exe.py")
    elif str(info.get("version")) != ver:
        problems.append(
            f"{asset.name} installs version {info.get('version')}, but pyproject.toml says "
            f"{ver}: build it again with tools\\make_setup_exe.py"
        )
    elif info.get("dirty") and not allow_dirty:
        problems.append(
            "the installer was built from code with uncommitted changes: commit (and push), "
            "build again, or pass --allow-dirty"
        )
    stamp, where = newest_source(sources)
    if where is not None and asset.stat().st_mtime < stamp:
        problems.append(
            "stale build: {} changed at {} but {} was built at {}; run tools\\make_setup_exe.py "
            "again".format(
                where.relative_to(ROOT).as_posix() if where.is_relative_to(ROOT) else where,
                datetime.fromtimestamp(stamp).isoformat(timespec="seconds"),
                asset.name,
                datetime.fromtimestamp(asset.stat().st_mtime).isoformat(timespec="seconds"),
            )
        )
    return problems


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manual_steps(ver: str, asset: Path, sha256: str, commit: str) -> str:
    size = asset.stat().st_size / 1048576 if asset.exists() else 0
    target = f"main (this build was made from commit {commit[:12]})" if commit else "main"
    return "\n".join(
        [
            f"No {TOKEN_ENV} is set, so nothing was uploaded. To publish 9Sigma Editor {ver} on "
            "the GitHub web page:",
            "",
            "  1. Push your commits first:  git push origin main",
            f"  2. Open https://github.com/{REPO}/releases/new",
            f'  3. Choose a tag: type v{ver} and pick "Create new tag: v{ver} on publish".',
            f"     Target: {target}.",
            f"  4. Release title: 9Sigma Editor {ver}",
            "  5. Describe what changed in a line or two (the app shows this text).",
            '  6. Drag this file into "Attach binaries by dropping them here":',
            f"       {asset}",
            f"       ({size:.0f} MB, SHA-256 {sha256})",
            '  7. Leave "Set as a pre-release" unticked, keep "Set as the latest release" ticked,',
            '     and click "Publish release".',
            f"  8. Check https://github.com/{REPO}/releases/latest: it shows {ver} with",
            f"     Install-9SigmaEditor-{ver}.exe attached. That page's address is also the",
            "     download link to give the team.",
            "",
            "Or let this script do it: create a fine-grained token on GitHub (Settings > Developer",
            f'settings > Fine-grained tokens) for {REPO} with "Contents: Read and write", then',
            f"  set {TOKEN_ENV}=<the token>",
            '  .venv\\Scripts\\python.exe tools\\publish_release.py "What changed"',
        ]
    )


# GitHub ---------------------------------------------------------------------------------------


def github_error(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"GitHub answered {response.status_code}"
    message = body.get("message", "") if isinstance(body, dict) else ""
    details = body.get("errors", []) if isinstance(body, dict) else []
    extra = "; ".join(
        str(item.get("message") or item.get("code") or item)
        if isinstance(item, dict)
        else str(item)
        for item in details
    )
    return f"GitHub answered {response.status_code}: {message}" + (f" ({extra})" if extra else "")


def publish(ver: str, asset: Path, sha256: str, notes: str, commit: str, token: str) -> int:
    import httpx

    tag = f"v{ver}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "9SigmaEditor-publish",
    }
    with httpx.Client(headers=headers, timeout=60.0) as client:
        say(f"release {tag} in {REPO}")
        found = client.get(f"{API}/repos/{REPO}/releases/tags/{tag}")
        if found.status_code == 200:
            release = found.json()
            say("the release exists already; the installer is added to it")
        elif found.status_code == 404:
            body: dict[str, Any] = {
                "tag_name": tag,
                "name": f"9Sigma Editor {ver}",
                "body": notes,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            }
            if commit:
                body["target_commitish"] = commit
            created = client.post(f"{API}/repos/{REPO}/releases", json=body)
            if created.status_code == 422 and commit:
                say(f"REFUSED: {github_error(created)}")
                say(f"GitHub does not have commit {commit[:12]} yet: push it (git push origin "
                    "main) and run this again.")
                return 1
            if created.status_code != 201:
                say(f"REFUSED: {github_error(created)}")
                if created.status_code in (401, 403, 404):
                    say(f"Check that {TOKEN_ENV} is a token that can write to {REPO}.")
                return 1
            release = created.json()
            say("release created")
        else:
            say(f"REFUSED: {github_error(found)}")
            if found.status_code in (401, 403):
                say(f"Check that {TOKEN_ENV} is a valid token for {REPO}.")
            return 1

        for existing in release.get("assets", []):
            if existing.get("name") == asset.name:
                if str(existing.get("digest", "")).lower() == f"sha256:{sha256}":
                    say("this exact installer is already attached")
                    break
                say("replacing the installer attached earlier")
                gone = client.delete(f"{API}/repos/{REPO}/releases/assets/{existing['id']}")
                if gone.status_code != 204:
                    say(f"REFUSED: {github_error(gone)}")
                    return 1
        else:
            size = asset.stat().st_size
            say(f"uploading {asset.name} ({size / 1048576:.0f} MB); this takes a while")
            with open(asset, "rb") as handle:
                uploaded = client.post(
                    f"{UPLOADS}/repos/{REPO}/releases/{release['id']}/assets",
                    params={"name": asset.name},
                    headers={"Content-Type": "application/octet-stream",
                             "Content-Length": str(size)},
                    content=handle,
                    timeout=httpx.Timeout(3600.0, connect=60.0),
                )
            if uploaded.status_code != 201:
                say(f"REFUSED: the upload failed. {github_error(uploaded)}")
                return 1
            say("uploaded: " + uploaded.json().get("browser_download_url", ""))

    # Read it back the way the app does: no token, the latest release.
    with httpx.Client(timeout=30.0, headers={"User-Agent": "9SigmaEditor-publish"}) as public:
        latest = public.get(f"{API}/repos/{REPO}/releases/latest")
    if latest.status_code != 200:
        say(f"WARNING: the latest release could not be read back ({latest.status_code}).")
        return 0
    served = latest.json()
    attached = {a.get("name"): a for a in served.get("assets", [])}
    mine = attached.get(asset.name, {})
    if served.get("tag_name") != tag:
        say(f"WARNING: GitHub's latest release is {served.get('tag_name')}, not {tag}. Mark "
            f"{tag} as the latest release on the release page.")
        return 0
    digest = str(mine.get("digest", "")).lower()
    if digest and digest != f"sha256:{sha256}":
        say("WARNING: the attached installer's checksum differs from the local file.")
        return 1
    say(f"the latest release is {tag} with {asset.name}"
        + (" (checksum matches)" if digest else ""))
    say()
    say(f"Done. Installed apps offer {ver} at their next check (at start, and every 6 hours).")
    say(f"Download link for the team: https://github.com/{REPO}/releases/latest")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    allow_dirty = "--allow-dirty" in args
    words = [word for word in args if word != "--allow-dirty"]
    ver = version()
    asset = asset_path(ver)
    print()
    problems = check_build(asset, ver, SOURCES, allow_dirty=allow_dirty)
    if problems:
        for problem in problems:
            say("REFUSED: " + problem)
        return 1
    info = embedded_info(asset)
    commit = str(info.get("commit") or "")
    sha256 = sha256_of(asset)
    say(f"{asset.name}: version {ver}, built {info.get('built', '?')}, "
        f"{asset.stat().st_size / 1048576:.0f} MB, SHA-256 {sha256}")
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        say()
        print(manual_steps(ver, asset, sha256, commit))
        print()
        return 2
    notes = " ".join(words).strip() or f"9Sigma Editor {ver}."
    return publish(ver, asset, sha256, notes, commit, token)


if __name__ == "__main__":
    raise SystemExit(main())
