"""
S3-compatible storage, against a mock S3 (moto). Skipped if moto is not installed
(pip install -r apps/backend/requirements-test.txt).
"""

from __future__ import annotations

import sys

import boto3
import pytest
from cryptography.fernet import Fernet

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from storage.file_storage import LocalEncryptedFileStorage, S3EncryptedFileStorage  # noqa: E402

KEY = Fernet.generate_key()
BUCKET = "reconflow-test"


@pytest.fixture()
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


class TestS3Storage:
    def test_round_trip_and_the_provider_only_ever_sees_ciphertext(self, s3):
        st = S3EncryptedFileStorage(BUCKET, KEY, client=s3)
        secret = b"order_id,amount\n1,100.00\n"
        st.save("uploads/a1/pos.csv", secret)
        assert st.read("uploads/a1/pos.csv") == secret
        stored = s3.get_object(Bucket=BUCKET, Key="uploads/a1/pos.csv")["Body"].read()
        assert b"100.00" not in stored and stored != secret          # encrypted before upload
        assert Fernet(KEY).decrypt(stored) == secret                # ...with the application's key

    def test_exists_delete_and_missing_files(self, s3):
        st = S3EncryptedFileStorage(BUCKET, KEY, client=s3)
        assert st.exists("x/y.bin") is False
        st.save("x/y.bin", b"1")
        assert st.exists("x/y.bin") is True
        st.delete("x/y.bin")
        st.delete("x/y.bin")  # idempotent, like the local backend
        assert st.exists("x/y.bin") is False
        with pytest.raises(FileNotFoundError):
            st.read("x/y.bin")  # the same error the local backend raises

    def test_prefix_keeps_the_application_in_its_own_folder(self, s3):
        st = S3EncryptedFileStorage(BUCKET, KEY, prefix="reconflow/", client=s3)
        st.save("a/b.csv", b"x")
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
        assert keys == ["reconflow/a/b.csv"]

    @pytest.mark.parametrize("bad", ["../secret", "a/../../b", "/abs/path", "", "a//b", "./a"])
    def test_unsafe_paths_are_refused(self, s3, bad):
        st = S3EncryptedFileStorage(BUCKET, KEY, client=s3)
        with pytest.raises(ValueError):
            st.save(bad, b"x")

    def test_files_written_by_the_local_backend_are_readable_here(self, s3, tmp_path):
        """Same format on both: a file can be moved between backends as is."""
        local = LocalEncryptedFileStorage(tmp_path, KEY)
        local.save("r/report.csv", b"report-bytes")
        raw = (tmp_path / "r" / "report.csv").read_bytes()
        s3.put_object(Bucket=BUCKET, Key="r/report.csv", Body=raw)
        assert S3EncryptedFileStorage(BUCKET, KEY, client=s3).read("r/report.csv") == b"report-bytes"

    def test_a_wrong_key_cannot_read(self, s3):
        S3EncryptedFileStorage(BUCKET, KEY, client=s3).save("k", b"data")
        from cryptography.fernet import InvalidToken

        with pytest.raises(InvalidToken):
            S3EncryptedFileStorage(BUCKET, Fernet.generate_key(), client=s3).read("k")


class TestFactory:
    def _fresh(self):
        for m in list(sys.modules):
            if m == "main" or m.startswith("bootstrap"):
                del sys.modules[m]
        import importlib

        return importlib.import_module("bootstrap.storage_factory")

    def test_default_is_local_disk(self, monkeypatch, tmp_path):
        monkeypatch.delenv("RECONFLOW_STORAGE_BACKEND", raising=False)
        monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path))
        monkeypatch.setenv("RECONFLOW_ENCRYPTION_KEY", KEY.decode())
        assert isinstance(self._fresh().build_storage(), LocalEncryptedFileStorage)

    def test_s3_needs_a_bucket_and_fails_fast_when_unreachable(self, s3, monkeypatch):
        monkeypatch.setenv("RECONFLOW_ENCRYPTION_KEY", KEY.decode())
        monkeypatch.setenv("RECONFLOW_STORAGE_BACKEND", "s3")
        monkeypatch.delenv("RECONFLOW_S3_BUCKET", raising=False)
        with pytest.raises(RuntimeError, match="RECONFLOW_S3_BUCKET"):
            self._fresh().build_storage()
        monkeypatch.setenv("RECONFLOW_S3_BUCKET", "no-such-bucket")
        with pytest.raises(RuntimeError, match="Cannot reach the S3 bucket"):
            self._fresh().build_storage()
        monkeypatch.setenv("RECONFLOW_S3_BUCKET", BUCKET)
        assert isinstance(self._fresh().build_storage(), S3EncryptedFileStorage)

    def test_unknown_backend_is_refused(self, monkeypatch):
        monkeypatch.setenv("RECONFLOW_ENCRYPTION_KEY", KEY.decode())
        monkeypatch.setenv("RECONFLOW_STORAGE_BACKEND", "dropbox")
        with pytest.raises(RuntimeError, match="Unknown RECONFLOW_STORAGE_BACKEND"):
            self._fresh().build_storage()


def test_the_whole_application_works_on_s3_storage(s3, tmp_path, monkeypatch):
    """Upload -> analysis -> report download, with every file kept in the bucket and none on disk."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RECONFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/s3.db")
    monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "local_should_stay_empty"))
    monkeypatch.setenv("RECONFLOW_ENCRYPTION_KEY", KEY.decode())
    monkeypatch.setenv("RECONFLOW_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("RECONFLOW_S3_BUCKET", BUCKET)
    monkeypatch.setenv("RECONFLOW_S3_PREFIX", "reconflow")
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_EMAIL", "op@x.test")
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", "operator-passphrase-1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for m in list(sys.modules):
        if m == "main" or m.startswith("bootstrap"):
            del sys.modules[m]
    import main

    with TestClient(main.app) as c:
        h = {"Authorization": "Bearer " + c.post("/auth/login", json={"email": "op@x.test", "password": "operator-passphrase-1"}).json()["token"]}
        org = c.post("/organizations", json={"legal_name": "O", "default_currency": "SAR"}, headers=h).json()["id"]
        br = c.post("/branches", json={"organization_id": org, "name": "B", "timezone": "Asia/Riyadh"}, headers=h).json()["id"]
        pid = c.post("/platforms", json={"name": "Talabat"}, headers=h).json()["id"]
        c.post("/contracts", json={"branch_id": br, "platform_id": pid, "commission_pct": "0.15", "valid_from": "2026-01-01", "valid_to": None}, headers=h)
        aid = c.post("/analyses", json={"branch_id": br, "period_start": "2026-03-01", "period_end": "2026-03-31"}, headers=h).json()["id"]
        pos = b"order_id,order_time,amount\n1,2026-03-01 10:00:00,100.00\n2,2026-03-02 11:00:00,80.00\n"
        plat = b"order_id,settlement_date,gross_amount,commission_amount\n1,2026-03-01 10:00:00,100.00,15.00\n"
        ids = []
        for st, name, content in (("POS_EXPORT", "pos.csv", pos), ("PLATFORM_SETTLEMENT", "s.csv", plat)):
            r = c.post(f"/analyses/{aid}/files", data={"source_type": st}, files={"file": (name, content, "text/csv")}, headers=h)
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        for f in ids:
            assert c.post(f"/uploaded-files/{f}/validation", headers=h).json()["status"] == "PASSED"
        c.post(f"/analyses/{aid}/mark-processing", headers=h)
        for f in ids:
            assert c.post(f"/uploaded-files/{f}/normalization", headers=h).status_code == 201
        for step in ("matching", "comparison", "discrepancies"):
            assert c.post(f"/analyses/{aid}/{step}", headers=h).status_code == 201
        c.post(f"/analyses/{aid}/mark-completed", headers=h)
        rep = c.post(f"/analyses/{aid}/reports", json={"format": "CSV"}, headers=h)
        assert rep.status_code == 201, rep.text
        dl = c.get(f"/reports/{rep.json()['id']}/download", headers=h)
        assert dl.status_code == 200 and b"MISSING" in dl.content.upper()

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) >= 3 and all(k.startswith("reconflow/") for k in keys)  # 2 uploads + the report
    assert not (tmp_path / "local_should_stay_empty").exists() or not any((tmp_path / "local_should_stay_empty").rglob("*.*"))
