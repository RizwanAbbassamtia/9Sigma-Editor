"""Sign-in: accounts, sessions, the protected API, the password rule and the command line."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import TEST_PASSWORD, sign_in
from typer.testing import CliRunner

from sigma_editor.app import create_app
from sigma_editor.auth.passwords import (
    check_password,
    generate_password,
    hash_password,
    verify_password,
)
from sigma_editor.auth.sessions import SESSION_COOKIE
from sigma_editor.auth.users import User, UserStore, write_users_file
from sigma_editor.cli import app as cli_app


def attempt(client: TestClient, username: str, password: str, remember: bool = False):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password, "remember": remember},
    )


def test_password_hashing_and_rule():
    stored = hash_password("correct horse")
    assert stored.startswith("pbkdf2_sha256$210000$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)
    assert not verify_password("x", "garbage")
    assert check_password("short") is not None
    assert check_password(" padded-pass ") is not None
    assert check_password("long enough") is None
    made = generate_password()
    assert made.startswith("Sigma-") and len(made) == 15
    assert check_password(made) is None


def test_status_login_me_logout(anon_client: TestClient):
    status = anon_client.get("/api/auth/status").json()
    assert status == {
        "auth_required": True, "accounts": 2, "signed_in": False, "user": None,
        "password_rule": "A password needs at least 8 characters.",
    }
    wrong = attempt(anon_client, "user1", "nope-nope")
    assert wrong.status_code == 401
    assert SESSION_COOKIE not in anon_client.cookies
    assert wrong.json()["detail"] == "That user name and password do not match."
    assert attempt(anon_client, "ghost", TEST_PASSWORD).status_code == 401

    ok = attempt(anon_client, " User1 ", TEST_PASSWORD)
    assert ok.status_code == 200
    assert ok.json() == {
        "username": "user1", "display_name": "User1", "enabled": True,
        "must_change_password": False,
    }
    assert SESSION_COOKIE in anon_client.cookies
    assert anon_client.get("/api/auth/me").json()["username"] == "user1"
    assert anon_client.get("/api/auth/status").json()["signed_in"] is True

    assert anon_client.post("/api/auth/logout").status_code == 204
    assert anon_client.get("/api/auth/me").status_code == 401


def test_protected_routes_need_a_session(anon_client: TestClient):
    refused = anon_client.get("/api/edits")
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Please sign in."}
    assert anon_client.post("/api/edits/x/update", json={"name": "y"}).status_code == 401
    assert anon_client.get("/api/system/info").status_code == 200
    assert anon_client.get("/api/openapi.json").status_code == 200
    assert anon_client.get("/").status_code == 200  # the app page itself is always served
    assert anon_client.get("/api/auth/status").status_code == 200


def test_signed_in_client_can_use_the_api(client: TestClient):
    assert client.get("/api/edits").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_change_password(app, client: TestClient):
    wrong = client.post(
        "/api/auth/change-password",
        json={"current_password": "not-it-at-all", "new_password": "a brand new one"},
    )
    assert wrong.status_code == 400
    short = client.post(
        "/api/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "tiny"},
    )
    assert short.status_code == 422
    with TestClient(app) as other:
        sign_in(other)
        assert other.get("/api/auth/me").status_code == 200
        changed = client.post(
            "/api/auth/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "a brand new one"},
        )
        assert changed.status_code == 200
        assert client.get("/api/auth/me").status_code == 200  # this browser stays signed in
        assert other.get("/api/auth/me").status_code == 401  # the other one must sign in again
        assert attempt(other, "user1", TEST_PASSWORD).status_code == 401
        assert attempt(other, "user1", "a brand new one").status_code == 200


def test_five_wrong_passwords_lock_the_account(anon_client: TestClient):
    for _ in range(5):
        assert attempt(anon_client, "user2", "wrong-one").status_code == 401
    locked = attempt(anon_client, "user2", TEST_PASSWORD)
    assert locked.status_code == 429
    assert "Try again in" in locked.json()["detail"]
    # Another account is not affected.
    assert attempt(anon_client, "user1", TEST_PASSWORD).status_code == 200


def test_disabled_account_cannot_sign_in_and_loses_its_session(app, data_dirs: dict[str, Path]):
    with TestClient(app) as client:
        sign_in(client, "user2")
        assert client.get("/api/auth/me").status_code == 200
        UserStore(data_dirs["app"]).set_enabled("user2", False)
        assert client.get("/api/auth/me").status_code == 401
        assert attempt(client, "user2", TEST_PASSWORD).status_code == 401


def test_session_survives_a_restart(app, data_dirs: dict[str, Path]):
    with TestClient(app) as client:
        assert attempt(client, "user1", TEST_PASSWORD, remember=True).status_code == 200
        token = client.cookies[SESSION_COOKIE]
        assert (data_dirs["app"] / "sessions.json").is_file()
    again = create_app()
    with TestClient(again) as fresh:
        fresh.cookies.set(SESSION_COOKIE, token)
        assert fresh.get("/api/auth/me").json()["username"] == "user1"


def test_shipped_accounts_are_copied_on_first_start(data_dirs: dict[str, Path]):
    (data_dirs["app"] / "users.json").unlink()
    shipped = [
        User(username="user7", display_name="User 7", password_hash=hash_password("seven-seven"))
    ]
    write_users_file(data_dirs["config"] / "users.json", shipped)
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["accounts"] == 1
        assert attempt(client, "user7", "seven-seven").status_code == 200
    assert (data_dirs["app"] / "users.json").is_file()
    # A later change to the shipped file does not touch this laptop's accounts.
    write_users_file(data_dirs["config"] / "users.json", [])
    with TestClient(create_app()) as client:
        assert client.get("/api/auth/status").json()["accounts"] == 1


def test_cli_users(data_dirs: dict[str, Path]):
    runner = CliRunner()
    listed = runner.invoke(cli_app, ["users", "list"])
    assert listed.exit_code == 0, listed.output
    assert "user1" in listed.output and "2 account(s)" in listed.output

    reset = runner.invoke(cli_app, ["users", "set-password", "user2", "Fresh-Pass-22"])
    assert reset.exit_code == 0, reset.output
    assert "user2: the password is now Fresh-Pass-22" in reset.output
    store = UserStore(data_dirs["app"])
    assert store.authenticate("user2", "Fresh-Pass-22") is not None
    assert store.authenticate("user2", TEST_PASSWORD) is None

    made_up = runner.invoke(cli_app, ["users", "set-password", "user1"])
    assert made_up.exit_code == 0
    password = made_up.output.strip().split()[-1]
    assert password.startswith("Sigma-")
    assert store.authenticate("user1", password) is not None

    too_short = runner.invoke(cli_app, ["users", "set-password", "user1", "short"])
    assert too_short.exit_code == 1

    added = runner.invoke(cli_app, ["users", "add", "user11", "--name", "Eleven"])
    assert added.exit_code == 0, added.output
    assert store.get("user11").display_name == "Eleven"
    twice = runner.invoke(cli_app, ["users", "add", "user11"])
    assert twice.exit_code == 1

    off = runner.invoke(cli_app, ["users", "enable", "user11", "--off"])
    assert off.exit_code == 0 and "off" in off.output
    assert store.get("user11").enabled is False
    missing = runner.invoke(cli_app, ["users", "enable", "nobody"])
    assert missing.exit_code == 1


@pytest.mark.parametrize("name", ["", "A", "1abc", "too-long-" * 5, "has space"])
def test_bad_usernames_are_refused(data_dirs: dict[str, Path], name: str):
    from sigma_editor.auth.users import InvalidUsername

    with pytest.raises(InvalidUsername):
        UserStore(data_dirs["app"]).add(name, "long enough password")
