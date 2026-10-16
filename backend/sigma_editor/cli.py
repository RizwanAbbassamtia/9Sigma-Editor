"""The ``sge`` command line: ``serve``, ``open``, ``doctor`` and ``users`` (the accounts)."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import typer
import uvicorn

from .config import HOST, load_settings
from .doctor import DoctorReport, check_webview2, run_all

app = typer.Typer(
    help="9Sigma Editor: a desktop editor for narrated picture-and-clip videos.",
    add_completion=False,
    no_args_is_help=True,
)
users_app = typer.Typer(
    help="The accounts that may use the app on this laptop (user1 to user10 to begin with).",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(users_app, name="users")

WINDOW_TITLE = "9Sigma Editor"
WINDOW_SIZE = (1440, 900)
START_TIMEOUT_SECONDS = 30
WEBVIEW_PROFILE_DIR = "webview"
"""The app window's own browser profile in ``<app_data_dir>``: page settings (the chosen
preview quality, the last tab) survive closing the app."""


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", help="Port on 127.0.0.1 (default 8766)."),
    reload: bool = typer.Option(False, "--reload", help="Restart when the code changes."),
) -> None:
    """Run the API server (and the built app screens) on 127.0.0.1."""
    settings = load_settings()
    chosen = port or settings.port
    typer.echo(f"9Sigma Editor API on http://{HOST}:{chosen}")
    uvicorn.run(
        "sigma_editor.app:create_app",
        factory=True,
        host=HOST,
        port=chosen,
        reload=reload,
        log_level="info",
    )


@app.command(name="open")
def open_window(
    port: int | None = typer.Option(None, "--port", help="Port on 127.0.0.1 (default 8766)."),
) -> None:
    """Start the server in the background and open the app in its own window."""
    settings = load_settings()
    chosen = port or settings.port
    url = f"http://{HOST}:{chosen}"

    server_thread: threading.Thread | None = None
    running = _our_server_answers(chosen)
    if running is True:
        typer.echo(f"Using the 9Sigma Editor server already running at {url}")
    elif running is False:
        typer.echo(
            f"Another program is already using port {chosen} on this PC, so 9Sigma Editor "
            f"cannot start there. Close that program, or run: sge open --port {chosen + 1}",
            err=True,
        )
        raise typer.Exit(code=1)
    else:
        server_thread = _start_server_thread(chosen)
        if not _wait_for_port(chosen, START_TIMEOUT_SECONDS):
            typer.echo(
                f"The app server did not start on port {chosen} within "
                f"{START_TIMEOUT_SECONDS} seconds. Run 'sge doctor' to see what is wrong.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"9Sigma Editor is running at {url}")

    if _open_native_window(url, settings.app_data_dir / WEBVIEW_PROFILE_DIR):
        return  # the window was closed; the daemon server thread ends with the process

    typer.echo("Opening 9Sigma Editor in your web browser instead.")
    webbrowser.open(url)
    if server_thread is not None:
        typer.echo("Keep this window open while you use the app. Press Ctrl+C to stop.")
        try:
            while server_thread.is_alive():
                server_thread.join(timeout=1)
        except KeyboardInterrupt:
            typer.echo("Stopped.")


@app.command()
def doctor() -> None:
    """Check Python, FFmpeg, fonts, folders and disk space. Exits with code 1 if anything
    fails."""
    report = run_all(load_settings())
    typer.echo(format_report(report))
    raise typer.Exit(code=0 if report.ok else 1)


# Accounts ------------------------------------------------------------------------------------


def _user_store() -> Any:
    from .auth.users import UserStore
    from .config import users_defaults_file

    settings = load_settings()
    return UserStore(settings.app_data_dir, users_defaults_file())


@users_app.command(name="list")
def users_list() -> None:
    """Show the accounts: user name, display name, on or off."""
    store = _user_store()
    users = store.load()
    if not users:
        typer.echo("No accounts yet. Run: sge users add <username>")
        raise typer.Exit(code=0)
    width = max(len(user.username) for user in users)
    for user in users:
        state = "on" if user.enabled else "off"
        typer.echo(f"{user.username:<{width}}  {user.name:<20}  {state}")
    typer.echo(f"\n{len(users)} account(s) in {store.path}")


@users_app.command(name="set-password")
def users_set_password(
    username: str = typer.Argument(..., help="The account, for example user3."),
    password: str | None = typer.Argument(
        None, help="The new password (at least 8 characters). Left out: one is made up."
    ),
) -> None:
    """Give an account a new password (made up when none is given) and print it."""
    from .auth.passwords import generate_password
    from .auth.users import UserStoreError

    chosen = password or generate_password()
    try:
        user = _user_store().set_password(username, chosen)
    except UserStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{user.username}: the password is now {chosen}")


@users_app.command(name="add")
def users_add(
    username: str = typer.Argument(..., help="A new account name, for example user11."),
    display_name: str = typer.Option("", "--name", help="The name shown in the app."),
) -> None:
    """Add an account with a made-up password and print it."""
    from .auth.passwords import generate_password
    from .auth.users import UserStoreError

    password = generate_password()
    try:
        user = _user_store().add(username, password, display_name)
    except UserStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{user.username} added. The password is {password}")


@users_app.command(name="enable")
def users_enable(
    username: str = typer.Argument(..., help="The account to turn on."),
    off: bool = typer.Option(False, "--off", help="Turn the account off instead."),
) -> None:
    """Turn an account on (or off with --off): an account that is off cannot sign in."""
    from .auth.users import UserStoreError

    try:
        user = _user_store().set_enabled(username, not off)
    except UserStoreError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{user.username} is now {'on' if user.enabled else 'off'}.")


# Helpers -------------------------------------------------------------------------------------


def format_report(report: DoctorReport) -> str:
    """A plain text table, no colours, readable in any terminal."""
    rows = [
        (check.status.upper(), check.name, check.detail, check.fix_hint)
        for check in report.checks
    ]
    headers = ("STATUS", "CHECK", "DETAIL", "HOW TO FIX")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row[:2]):
            widths[index] = max(widths[index], len(cell))
    lines = [
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]}",
        "-" * (widths[0] + widths[1] + 4 + len(headers[2])),
    ]
    for status, name, detail, fix_hint in rows:
        lines.append(f"{status:<{widths[0]}}  {name:<{widths[1]}}  {detail}")
        if fix_hint:
            pad = " " * (widths[0] + widths[1] + 4)
            lines.append(f"{pad}{headers[3].title()}: {fix_hint}")
    counts = {
        status: sum(1 for check in report.checks if check.status == status)
        for status in ("ok", "warn", "fail")
    }
    if not report.ok:
        summary = "Some checks failed."
    elif counts["warn"]:
        summary = "Nothing failed, but some warnings need attention."
    else:
        summary = "All checks passed."
    lines.append("")
    lines.append(
        f"{summary} {counts['ok']} ok, {counts['warn']} warnings, {counts['fail']} failed."
    )
    return "\n".join(lines)


def _port_answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _our_server_answers(port: int, timeout: float = 2.0) -> bool | None:
    """``True`` if 9Sigma Editor answers on the port, ``False`` if something else does,
    ``None`` if nothing is listening."""
    if not _port_answers(port):
        return None
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/system/info", timeout=timeout
        ) as response:
            info = json.load(response)
    except Exception:  # not HTTP, not JSON, or an error page: not our server
        return False
    return isinstance(info, dict) and {"version", "app_data_dir"} <= set(info)


def _wait_for_port(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _port_answers(port):
            return True
        time.sleep(0.2)
    return False


def _start_server_thread(port: int) -> threading.Thread:
    from .app import create_app

    config = uvicorn.Config(create_app(), host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="sge-server", daemon=True)
    thread.start()
    return thread


def _open_native_window(url: str, profile_dir: Path) -> bool:
    """Show the app in a pywebview window. Returns False if that is not possible.

    The window keeps a persistent browser profile in ``profile_dir`` (pywebview's default is
    a private profile that forgets every page setting whenever the window starts).
    """
    if sys.platform == "win32" and check_webview2().status != "ok":
        typer.echo(
            "The Microsoft Edge WebView2 runtime is not installed, so the app cannot open "
            "in its own window.",
            err=True,
        )
        return False
    try:
        import webview
    except Exception as exc:  # pywebview missing or its platform bits failed to load
        typer.echo(f"The app window could not be prepared: {exc}", err=True)
        return False
    try:
        # "Save captions as SRT" is a file download, which pywebview refuses unless downloads
        # are switched on (the file goes to the Downloads folder).
        window_settings = getattr(webview, "settings", None)
        if isinstance(window_settings, MutableMapping) and "ALLOW_DOWNLOADS" in window_settings:
            window_settings["ALLOW_DOWNLOADS"] = True
        profile_dir.mkdir(parents=True, exist_ok=True)
        webview.create_window(WINDOW_TITLE, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1])
        webview.start(private_mode=False, storage_path=str(profile_dir))
    except Exception as exc:
        typer.echo(f"The app window could not be opened: {exc}", err=True)
        return False
    return True


if __name__ == "__main__":
    app()
