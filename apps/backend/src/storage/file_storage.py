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


class S3EncryptedFileStorage(FileStorage):
    """The same contract as LocalEncryptedFileStorage, on any S3-compatible
    object store (AWS S3, Cloudflare R2, Backblaze B2, MinIO, ...).

    Files are encrypted with the same Fernet key BEFORE they leave this
    process, so the storage provider only ever holds ciphertext - you do not
    have to trust the provider with client financial data, and objects written
    by the local backend and this one use the same format.

    Switched on by RECONFLOW_STORAGE_BACKEND=s3 (see bootstrap/storage_factory.py);
    the default stays local disk. boto3 is imported lazily so the local-only
    install never loads it.
    """

    def __init__(
        self,
        bucket: str,
        encryption_key: bytes,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client=None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region_name or None)
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._fernet = Fernet(encryption_key)

    def _key(self, path: str) -> str:
        parts = path.replace("\\", "/").split("/")
        if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
            # Same defence as the local backend: never build a key from a path
            # that could point outside this application's area of the bucket.
            raise ValueError(f"Unsafe storage path: {path!r}")
        return self._prefix + "/".join(parts)

    def check_access(self) -> None:
        """Fail fast at start-up (wrong bucket name, bad credentials) rather than on the first upload."""
        self._client.head_bucket(Bucket=self._bucket)

    def save(self, path: str, content: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._key(path), Body=self._fernet.encrypt(content))

    def read(self, path: str) -> bytes:
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=self._key(path))
        except self._client.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(path) from exc  # same error the local backend raises
        return self._fernet.decrypt(obj["Body"].read())

    def delete(self, path: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(path))  # S3 delete is idempotent

    def exists(self, path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=self._key(path))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
