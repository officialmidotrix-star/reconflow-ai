"""
Security-related helpers for the Data Import module.

These are file-level guardrails only - none of this inspects file *content*
in a business sense (that's Data Validation's job). It answers narrower
questions: is this a safe filename, is this an allowed extension, is this
file's checksum X, and (via the MalwareScanner interface) is this file
flagged by whatever scanning capability the deployment has configured.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Protocol

ALLOWED_EXTENSIONS = frozenset({"csv", "xlsx", "xls"})

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(original_filename: str) -> str:
    """Strip any directory component and collapse anything that isn't a
    safe filename character, so this string can never be used to escape the
    intended storage directory (no `..`, no `/`, no null bytes, etc.).

    The original filename is still stored verbatim in `original_filename`
    for display purposes - this function only produces the sanitized form
    used when constructing an on-disk path.
    """
    # Take only the final path component; anything before it (e.g. from a
    # browser that sent a full local path) is discarded outright.
    name = PurePosixPath(original_filename.replace("\\", "/")).name
    name = name.strip()
    if not name or name in {".", ".."}:
        name = "upload"
    return _SAFE_FILENAME_RE.sub("_", name)


def file_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def is_allowed_extension(filename: str) -> bool:
    return file_extension(filename) in ALLOWED_EXTENSIONS


def compute_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class MalwareScanner(Protocol):
    def scan(self, content: bytes) -> bool:
        """Return True if the content is clean, False if it should be
        rejected."""
        ...


class NoOpMalwareScanner:
    """Placeholder scanner for MVP.

    This intentionally does NOT perform real malware detection - it exists
    purely as the extension point described in the design doc (section 10 /
    11) so a real scanning engine can be dropped in later without touching
    any calling code. Do not treat `scan()` returning True as a security
    guarantee.
    """

    def scan(self, content: bytes) -> bool:  # noqa: ARG002 - stub
        return True
