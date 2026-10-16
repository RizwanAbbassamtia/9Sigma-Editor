"""The accounts that may use the app: ``<app_data_dir>/users.json``.

The app ships with ``config/users.json`` (the ten preset accounts, hashed passwords); the
first start copies it into the app data folder, and from then on that copy is the truth (a
changed password stays across updates). Only the hashes are ever stored or returned.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..storage.files import atomic_write_text
from .passwords import check_password, hash_password, verify_password

USERS_FILE = "users.json"
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")
LOCK = threading.RLock()


class UserStoreError(Exception):
    """Plain English, safe to show."""


class UserNotFound(UserStoreError):
    pass


class InvalidUsername(UserStoreError):
    pass


class User(BaseModel):
    username: str
    display_name: str = ""
    password_hash: str
    enabled: bool = True
    must_change_password: bool = False
    updated_at: datetime | None = None

    @property
    def name(self) -> str:
        return self.display_name or self.username


class PublicUser(BaseModel):
    """What the API returns about an account: never the hash."""

    username: str
    display_name: str
    enabled: bool = True
    must_change_password: bool = False


class UsersDoc(BaseModel):
    users: list[User] = Field(default=[])
    schema_version: int = 1


def public_of(user: User) -> PublicUser:
    return PublicUser(
        username=user.username,
        display_name=user.name,
        enabled=user.enabled,
        must_change_password=user.must_change_password,
    )


def check_username(username: str) -> str:
    name = str(username or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise InvalidUsername(
            "A user name is 2 to 32 characters: small letters, digits, dots, dashes or "
            "underscores, starting with a letter (for example user1)."
        )
    return name


def read_users_file(path: Path) -> list[User] | None:
    """The accounts in ``path``, or ``None`` when the file is missing or cannot be read."""
    try:
        doc = UsersDoc.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None
    return list(doc.users)


def write_users_file(path: Path, users: list[User]) -> None:
    doc = UsersDoc(users=users)
    atomic_write_text(Path(path), doc.model_dump_json(indent=2) + "\n")


class UserStore:
    """Reads and writes the accounts of this laptop."""

    def __init__(self, app_data_dir: Path | str, defaults_file: Path | str | None = None) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.path = self.app_data_dir / USERS_FILE
        self.defaults_file = Path(defaults_file) if defaults_file is not None else None

    def load(self) -> list[User]:
        """The accounts: this laptop's file, else the shipped defaults (copied here once)."""
        with LOCK:
            users = read_users_file(self.path)
            if users is not None:
                return users
            defaults = read_users_file(self.defaults_file) if self.defaults_file else None
            if not defaults:
                return []
            try:
                write_users_file(self.path, defaults)
            except OSError:
                pass  # read-only app data: the defaults still work for this run
            return defaults

    def save(self, users: list[User]) -> None:
        with LOCK:
            write_users_file(self.path, users)

    def get(self, username: str) -> User | None:
        wanted = str(username or "").strip().lower()
        for user in self.load():
            if user.username == wanted:
                return user
        return None

    def public(self) -> list[PublicUser]:
        return [public_of(user) for user in self.load()]

    def authenticate(self, username: str, password: str) -> User | None:
        """The account when the name and password match and the account is on; else None.
        A missing account still costs a hash check, so timing does not reveal names."""
        user = self.get(username)
        stored = user.password_hash if user is not None else _dummy_hash()
        if not verify_password(str(password or ""), stored):
            return None
        if user is None or not user.enabled:
            return None
        return user

    def set_password(self, username: str, password: str, *, must_change: bool = False) -> User:
        problem = check_password(password)
        if problem:
            raise UserStoreError(problem)
        with LOCK:
            users = self.load()
            for index, user in enumerate(users):
                if user.username == str(username or "").strip().lower():
                    changed = user.model_copy(
                        update={
                            "password_hash": hash_password(password),
                            "must_change_password": must_change,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    users[index] = changed
                    self.save(users)
                    return changed
        raise UserNotFound(f"There is no account called '{username}'.")

    def add(self, username: str, password: str, display_name: str = "") -> User:
        name = check_username(username)
        problem = check_password(password)
        if problem:
            raise UserStoreError(problem)
        with LOCK:
            users = self.load()
            if any(user.username == name for user in users):
                raise UserStoreError(f"The account '{name}' exists already.")
            user = User(
                username=name,
                display_name=" ".join(str(display_name or "").split()),
                password_hash=hash_password(password),
                updated_at=datetime.now(UTC),
            )
            users.append(user)
            self.save(users)
            return user

    def set_enabled(self, username: str, enabled: bool) -> User:
        with LOCK:
            users = self.load()
            for index, user in enumerate(users):
                if user.username == str(username or "").strip().lower():
                    changed = user.model_copy(
                        update={"enabled": enabled, "updated_at": datetime.now(UTC)}
                    )
                    users[index] = changed
                    self.save(users)
                    return changed
        raise UserNotFound(f"There is no account called '{username}'.")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A hash to check against when the account does not exist (same cost as a real one)."""
    return hash_password("not-a-real-password")


def preset_accounts(count: int = 10) -> list[tuple[str, str]]:
    """``(username, display name)`` of the shipped accounts: user1 to user<count>."""
    return [(f"user{number}", f"User {number}") for number in range(1, count + 1)]


def make_default_users(passwords: dict[str, str]) -> list[User]:
    """Accounts for ``{username: password}`` with the passwords hashed."""
    now = datetime.now(UTC)
    return [
        User(
            username=check_username(username),
            display_name=f"User {username[4:]}" if username.startswith("user") else username,
            password_hash=hash_password(password),
            updated_at=now,
        )
        for username, password in passwords.items()
    ]


def dump_users(users: list[User]) -> str:
    return json.dumps(UsersDoc(users=users).model_dump(mode="json"), indent=2) + "\n"
