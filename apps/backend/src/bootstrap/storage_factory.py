"""
Chooses where uploaded files and generated reports are kept.

  RECONFLOW_STORAGE_BACKEND = local   (default) encrypted files in RECONFLOW_STORAGE_DIR
                              s3      any S3-compatible object store (survives redeploys)

For s3:
  RECONFLOW_S3_BUCKET        required
  RECONFLOW_S3_ENDPOINT_URL  optional - the provider's address (needed for Cloudflare R2,
                             Backblaze B2, MinIO...; leave empty for AWS S3)
  RECONFLOW_S3_REGION        optional
  RECONFLOW_S3_PREFIX        optional - a folder inside the bucket, e.g. "reconflow/"
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   the access key pair (standard names)

Files are encrypted with RECONFLOW_ENCRYPTION_KEY before upload either way.
"""

from __future__ import annotations

import logging
import os

from storage.file_storage import FileStorage, LocalEncryptedFileStorage, S3EncryptedFileStorage

from .config import get_encryption_key, get_storage_dir

logger = logging.getLogger("reconflow.bootstrap.storage")


def build_storage() -> FileStorage:
    backend = (os.environ.get("RECONFLOW_STORAGE_BACKEND") or "local").strip().lower()
    key = get_encryption_key()

    if backend == "local":
        return LocalEncryptedFileStorage(get_storage_dir(), key)

    if backend == "s3":
        bucket = os.environ.get("RECONFLOW_S3_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("RECONFLOW_STORAGE_BACKEND=s3 needs RECONFLOW_S3_BUCKET to be set.")
        storage = S3EncryptedFileStorage(
            bucket, key,
            prefix=os.environ.get("RECONFLOW_S3_PREFIX", ""),
            endpoint_url=os.environ.get("RECONFLOW_S3_ENDPOINT_URL") or None,
            region_name=os.environ.get("RECONFLOW_S3_REGION") or None,
        )
        try:
            storage.check_access()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Cannot reach the S3 bucket {bucket!r} ({type(exc).__name__}). Check RECONFLOW_S3_BUCKET, "
                "RECONFLOW_S3_ENDPOINT_URL, RECONFLOW_S3_REGION and the access key pair."
            ) from exc
        logger.warning("File storage: S3-compatible bucket %r.", bucket)
        return storage

    raise RuntimeError(f"Unknown RECONFLOW_STORAGE_BACKEND {backend!r} (use 'local' or 's3').")
