"""
Security helpers for the Identity & Access module.

Password hashing uses salted PBKDF2-HMAC-SHA256 from the standard
library - no new dependency, and a well-understood, slow, salted KDF
appropriate for passwords (unlike a bare hashlib.sha256, which is fast and
therefore wrong for this purpose). Session tokens are random and opaque;
only their SHA-256 hash is ever persisted (see models.py's Session
docstring for why).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

PBKDF2_ITERATIONS = 600_000  # current OWASP-recommended floor for PBKDF2-SHA256
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)  # constant-time, avoids timing attacks


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
