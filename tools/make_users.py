"""Make the accounts the app ships with: ``config/users.json`` (user1 to user10, hashed
passwords) and ``config/local.user-passwords.txt`` (the passwords in clear, for the owner to
hand out; git-ignored, never committed).

    .venv\\Scripts\\python.exe tools\\make_users.py            new passwords for all ten
    .venv\\Scripts\\python.exe tools\\make_users.py --count 12  more accounts

Running it again replaces every shipped password. Accounts already copied to a laptop's
``%LOCALAPPDATA%\\9SigmaEditor\\users.json`` keep their passwords there.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sigma_editor.auth.passwords import generate_password  # noqa: E402
from sigma_editor.auth.users import (  # noqa: E402
    dump_users,
    make_default_users,
    preset_accounts,
)

USERS_FILE = ROOT / "config" / "users.json"
PASSWORDS_FILE = ROOT / "config" / "local.user-passwords.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the shipped accounts.")
    parser.add_argument("--count", type=int, default=10, help="how many accounts (default 10)")
    args = parser.parse_args(argv)
    passwords = {username: generate_password() for username, _ in preset_accounts(args.count)}
    users = make_default_users(passwords)
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(dump_users(users), encoding="utf-8", newline="\n")
    lines = [
        "9Sigma Editor: the accounts and their initial passwords",
        f"Made on {datetime.now():%Y-%m-%d %H:%M}. Hand each person their line. Keep this "
        "file private.",
        "A person can change their password in the app (Settings > Account);",
        "'sge users set-password <username>' makes a new one.",
        "",
    ]
    width = max(len(name) for name in passwords)
    lines.extend(f"{name:<{width}}  {password}" for name, password in passwords.items())
    PASSWORDS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {USERS_FILE.relative_to(ROOT)} ({len(users)} accounts, hashed)")
    print(f"wrote {PASSWORDS_FILE.relative_to(ROOT)} (the passwords; git-ignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
