"""Withdraw a published version, so the installed apps stop offering it.

    set GITHUB_TOKEN=<token>
    .venv\\Scripts\\python.exe tools\\withdraw_release.py 0.2.0
    .venv\\Scripts\\python.exe tools\\withdraw_release.py 0.2.0 --yes     (no question)

Deletes release ``v<version>`` and its tag in RizwanAbbassamtia/9Sigma-Editor, so GitHub's
"latest release" (what the app reads, docs/RELEASE-CONTRACT.md section 4) is the previous one
again, and reads it back the way the app does. Nothing is built or uploaded.

A laptop that already installed the withdrawn version stays on it: the app never offers a
version older than the one it runs. To move everyone forward, publish a newer version with
tools/publish_release.py.

The token comes from ``GITHUB_TOKEN`` only (never from git's credential store) and is never
printed. Without it the steps for the GitHub web page are printed and the exit code is 2.
"""

from __future__ import annotations

import os
import sys

REPO = "RizwanAbbassamtia/9Sigma-Editor"
API = "https://api.github.com"
TOKEN_ENV = "GITHUB_TOKEN"


def say(text: str = "") -> None:
    print(("  " + text) if text else "", flush=True)


def manual_steps(ver: str) -> str:
    return "\n".join(
        [
            f"No {TOKEN_ENV} is set, so nothing was changed. To withdraw {ver} on the GitHub web "
            "page:",
            "",
            f"  1. Open https://github.com/{REPO}/releases/tag/v{ver}",
            '  2. Click the bin icon (Delete) at the top right of the release and confirm.',
            f"  3. Open https://github.com/{REPO}/tags, open v{ver}, and delete the tag too.",
            f"  4. Check https://github.com/{REPO}/releases/latest: it shows the version the",
            "     team should have.",
            "",
            f"Or set {TOKEN_ENV} to a token for {REPO} with \"Contents: Read and write\" and run",
            "this script again.",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    versions = [word.lstrip("vV") for word in args if not word.startswith("--")]
    if len(versions) != 1:
        print(__doc__)
        return 2
    ver = versions[0]
    tag = f"v{ver}"
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print()
        print(manual_steps(ver))
        print()
        return 2

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "9SigmaEditor-withdraw",
    }
    with httpx.Client(headers=headers, timeout=60.0) as client:
        found = client.get(f"{API}/repos/{REPO}/releases/tags/{tag}")
        if found.status_code == 404:
            say(f"There is no release {tag}; nothing to withdraw.")
            release = None
        elif found.status_code != 200:
            say(f"GitHub answered {found.status_code}; check that {TOKEN_ENV} can write to {REPO}.")
            return 1
        else:
            release = found.json()
        if "--yes" not in args:
            answer = input(
                f"  Delete release {tag} and its tag from {REPO}? Laptops that installed it "
                "keep it; the others are offered the previous release. Type yes: "
            )
            if answer.strip().lower() != "yes":
                say("Nothing was changed.")
                return 1
        if release is not None:
            gone = client.delete(f"{API}/repos/{REPO}/releases/{release['id']}")
            if gone.status_code != 204:
                say(f"The release could not be deleted (GitHub answered {gone.status_code}).")
                return 1
            say(f"release {tag} deleted")
        ref = client.delete(f"{API}/repos/{REPO}/git/refs/tags/{tag}")
        say(f"tag {tag} " + ("deleted" if ref.status_code == 204 else "was not there"))

    with httpx.Client(timeout=30.0, headers={"User-Agent": "9SigmaEditor-withdraw"}) as public:
        latest = public.get(f"{API}/repos/{REPO}/releases/latest")
    if latest.status_code == 200:
        say(f"The app is now offered {latest.json().get('tag_name')} (GitHub's latest release).")
    elif latest.status_code == 404:
        say("No release is published now; the app offers nothing.")
    else:
        say(f"The latest release could not be read back ({latest.status_code}); check the page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
