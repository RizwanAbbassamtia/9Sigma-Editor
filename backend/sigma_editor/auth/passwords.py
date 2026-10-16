"""Password hashing and the password rule.

Hashes are PBKDF2-HMAC-SHA256 with a random 16-byte salt and 210,000 rounds, stored as
``pbkdf2_sha256$<rounds>$<salt base64>$<hash base64>``. A password is never kept or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 210_000
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
PASSWORD_RULE = f"A password needs at least {MIN_PASSWORD_LENGTH} characters."
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0, O, 1 or I: easy to read out


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join((ALGORITHM, str(iterations), _b64(salt), _b64(digest)))


def verify_password(password: str, stored: str) -> bool:
    """True when ``password`` matches the stored hash (constant-time comparison)."""
    try:
        algorithm, rounds, salt, digest = str(stored).split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(rounds)
        salt_bytes = base64.b64decode(salt)
        expected = base64.b64decode(digest)
    except (TypeError, ValueError):
        return False
    if iterations < 1000 or not salt_bytes or not expected:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return hmac.compare_digest(actual, expected)


def check_password(password: str) -> str | None:
    """The plain-English problem with a new password, or ``None`` when it is fine."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return PASSWORD_RULE
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"A password can have at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip() != password:
        return "A password cannot start or end with a space."
    return None


def generate_password() -> str:
    """A fresh password that is easy to read out: ``Sigma-XXXX-XXXX``."""
    parts = ("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(2))
    return "Sigma-" + "-".join(parts)
