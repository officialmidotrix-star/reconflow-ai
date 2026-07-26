"""
Generic encrypted file storage. `FileStorage` is the interface every
caller depends on; the MVP implementation writes encrypted files to a
local directory (matching the self-hosted, per-customer deployment model
from Phase 2). Swapping to an S3-compatible backend later means writing
one new class that satisfies this same interface - nothing calling it has
to change.

Not specific to any one module's data - Data Import uses it for uploaded
files, Reporting & Export uses it for generated reports. Path *scheme*
(what the string looks like) stays with each calling module, since that's
specific to what's being stored; only the storage mechanism itself is
shared here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet


class FileStorage(ABC):
    @abstractmethod
    def save(self, path: str, content: bytes) -> None: ...

    @abstractmethod
    def read(self, path: str) -> bytes: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...


class LocalEncryptedFileStorage(FileStorage):
    """Encrypts file bytes with a symmetric key before writing to disk, and
    decrypts on read. The key itself is supplied at construction time and is
    expected to come from the platform's secrets/config layer - this class
    does not generate, store, or rotate keys itself.
    """

    def __init__(self, base_dir: str | Path, encryption_key: bytes) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(encryption_key)

    def _resolve(self, path: str) -> Path:
        full_path = (self._base_dir / path).resolve()
        if self._base_dir.resolve() not in full_path.parents and full_path != self._base_dir.resolve():
            # Defense in depth: even though `path` is always built internally
            # by each caller from sanitized/deterministic components, refuse
            # to ever write/read outside base_dir.
            raise ValueError(f"Resolved path escapes storage root: {path}")
        return full_path

    def save(self, path: str, content: bytes) -> None:
        full_path = self._resolve(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet.encrypt(content)
        full_path.write_bytes(encrypted)

    def read(self, path: str) -> bytes:
        full_path = self._resolve(path)
        encrypted = full_path.read_bytes()
        return self._fernet.decrypt(encrypted)

    def delete(self, path: str) -> None:
        full_path = self._resolve(path)
        full_path.unlink(missing_ok=True)

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()
