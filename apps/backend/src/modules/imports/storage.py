"""
File storage for the Data Import module.

FileStorage and LocalEncryptedFileStorage now live in the shared storage/
package (promoted once Reporting & Export needed the same generic
capability) - re-exported here so existing imports of this module keep
working unchanged. build_storage_path stays here: it's Data Import's own
path scheme, not a generic storage concern.
"""

from __future__ import annotations

from storage.file_storage import FileStorage, LocalEncryptedFileStorage

__all__ = ["FileStorage", "LocalEncryptedFileStorage", "build_storage_path"]


def build_storage_path(
    *, branch_id: str, analysis_id: str, source_type: str, version: int, extension: str
) -> str:
    """Deterministic, collision-free path for one uploaded file version.

    Scoped by branch and analysis (not by organization, since one deployment
    = one organization per decision 2/3 in Phase 2 - there is never another
    organization's data to keep separate from within a single deployment).
    """
    ext = f".{extension}" if extension else ""
    return f"{branch_id}/{analysis_id}/{source_type}/v{version}{ext}"
