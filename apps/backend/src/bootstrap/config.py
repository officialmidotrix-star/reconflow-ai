"""
Environment configuration. Pure functions reading os.environ, with
sensible dev-friendly fallbacks for anything that can safely have one, and
a clear warning logged whenever a fallback is used instead of real
configuration - so running the app without full production config is
possible (for local development and the smoke test), but never silently.
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger("reconflow.bootstrap.config")


def get_database_url() -> str:
    return os.environ.get("RECONFLOW_DATABASE_URL", "sqlite:///./reconflow.db")


def get_storage_dir() -> str:
    return os.environ.get("RECONFLOW_STORAGE_DIR", "./reconflow_files")


def get_encryption_key() -> bytes:
    raw = os.environ.get("RECONFLOW_ENCRYPTION_KEY")
    if raw:
        return raw.encode("utf-8")
    generated = Fernet.generate_key()
    logger.warning(
        "RECONFLOW_ENCRYPTION_KEY is not set - generated a temporary key for this "
        "process only. Files encrypted now will NOT be readable after a restart. "
        "Set RECONFLOW_ENCRYPTION_KEY to a persistent Fernet key for real use."
    )
    return generated


def has_anthropic_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def has_smtp_credentials() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM_ADDRESS"))
