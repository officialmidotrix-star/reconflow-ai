"""
Unit tests for IdentityAccessService.

The last test class mirrors Analysis Orchestration's own integration
test: it builds a real AuthContext via IdentityAccessService and hands it
straight to a real ImportService.import_file() call, proving the shape
Data Import declared all the way back at the start of this build is now
satisfied by a real implementation, not a hand-built stand-in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.identity_access.dependencies import InMemoryAuditLogger
from modules.identity_access.exceptions import (
    EmailAlreadyExistsError,
    InsufficientRoleError,
    InvalidCredentialsError,
    InvalidSessionError,
    UserNotFoundError,
)
from modules.identity_access.models import UserRole
from modules.identity_access.service import IdentityAccessService
from modules.imports.models import Base

BRANCH_ID = "branch-1"

# "branches" is now a real table (Organization & Branch Management) -
# importing its model registers it, replacing the stand-in this file
# used before that module existed.
from modules.organizations.models import Branch  # noqa: E402,F401


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, audit_logger):
    return IdentityAccessService(db=db, audit_logger=audit_logger)


def _create_user(service, email="owner@example.com", password="correct horse battery staple"):
    return service.create_user(email=email, password=password, role=UserRole.OWNER)


class TestCreateUser:
    def test_hashes_password_not_plaintext(self, service, db):
        user = _create_user(service, password="hunter2")
        assert user.password_hash != "hunter2"
        assert "hunter2" not in user.password_hash

    def test_duplicate_email_raises(self, service):
        _create_user(service, email="dup@example.com")
        with pytest.raises(EmailAlreadyExistsError):
            _create_user(service, email="dup@example.com")

    def test_same_password_different_users_get_different_hashes(self, service):
        user_a = _create_user(service, email="a@example.com", password="same-password")
        user_b = _create_user(service, email="b@example.com", password="same-password")
        assert user_a.password_hash != user_b.password_hash  # unique salts


class TestAuthenticate:
    def test_success_returns_working_token(self, service):
        _create_user(service, email="owner@example.com", password="s3cret!")
        token, user = service.authenticate(email="owner@example.com", password="s3cret!")
        assert token
        resolved = service.get_current_user(token=token)
        assert resolved.id == user.id

    def test_wrong_password_raises(self, service):
        _create_user(service, email="owner@example.com", password="s3cret!")
        with pytest.raises(InvalidCredentialsError):
            service.authenticate(email="owner@example.com", password="wrong")

    def test_unknown_email_raises(self, service):
        with pytest.raises(InvalidCredentialsError):
            service.authenticate(email="nobody@example.com", password="whatever")


class TestGetCurrentUser:
    def test_invalid_token_raises(self, service):
        with pytest.raises(InvalidSessionError):
            service.get_current_user(token="not-a-real-token")

    def test_expired_session_raises(self, db, audit_logger):
        service = IdentityAccessService(
            db=db, audit_logger=audit_logger, session_lifetime=timedelta(seconds=-1)
        )
        _create_user(service, email="owner@example.com", password="s3cret!")
        token, _ = service.authenticate(email="owner@example.com", password="s3cret!")
        with pytest.raises(InvalidSessionError):
            service.get_current_user(token=token)

    def test_revoked_session_raises(self, service):
        _create_user(service, email="owner@example.com", password="s3cret!")
        token, _ = service.authenticate(email="owner@example.com", password="s3cret!")
        service.revoke_session(token=token)
        with pytest.raises(InvalidSessionError):
            service.get_current_user(token=token)


class TestRevokeSession:
    def test_revoking_twice_is_a_noop(self, service):
        _create_user(service, email="owner@example.com", password="s3cret!")
        token, _ = service.authenticate(email="owner@example.com", password="s3cret!")
        service.revoke_session(token=token)
        service.revoke_session(token=token)  # should not raise


class TestBranchAccess:
    def test_grant_and_read_back(self, service):
        user = _create_user(service)
        service.grant_branch_access(user_id=user.id, branch_id=BRANCH_ID, requested_by=user.id)
        assert service.get_accessible_branch_ids(user_id=user.id) == frozenset({BRANCH_ID})

    def test_granting_twice_is_idempotent(self, service):
        user = _create_user(service)
        service.grant_branch_access(user_id=user.id, branch_id=BRANCH_ID, requested_by=user.id)
        service.grant_branch_access(user_id=user.id, branch_id=BRANCH_ID, requested_by=user.id)
        assert service.get_accessible_branch_ids(user_id=user.id) == frozenset({BRANCH_ID})

    def test_unknown_user_raises(self, service):
        with pytest.raises(UserNotFoundError):
            service.grant_branch_access(
                user_id="does-not-exist", branch_id=BRANCH_ID, requested_by="someone"
            )


class TestEnsureRole:
    def test_allows_matching_role(self, service):
        user = _create_user(service)  # OWNER
        service.ensure_role(user, allowed={UserRole.OWNER, UserRole.FINANCE_MANAGER})

    def test_denies_non_matching_role(self, service):
        user = _create_user(service)  # OWNER
        with pytest.raises(InsufficientRoleError):
            service.ensure_role(user, allowed={UserRole.AUDITOR})


class TestAuditLogging:
    def test_creation_login_logout_all_logged(self, service, audit_logger):
        user = _create_user(service, email="owner@example.com", password="s3cret!")
        assert audit_logger.records[0].event == "user_created"

        token, _ = service.authenticate(email="owner@example.com", password="s3cret!")
        assert audit_logger.records[1].event == "user_logged_in"
        assert audit_logger.records[1].user_id == user.id

        service.revoke_session(token=token)
        assert audit_logger.records[2].event == "user_logged_out"


class TestSatisfiesDataImportAuthContext:
    def test_real_auth_context_works_with_a_real_import_service_call(self, db, tmp_path, service):
        from cryptography.fernet import Fernet

        from modules.imports.dependencies import AnalysisStatus, InMemoryAnalysisLookup
        from modules.imports.dependencies import InMemoryAuditLogger as ImportAuditLogger
        from modules.imports.models import SourceType
        from modules.imports.security import NoOpMalwareScanner
        from modules.imports.service import ImportService
        from storage.file_storage import LocalEncryptedFileStorage

        user = _create_user(service, email="owner@example.com", password="s3cret!")
        service.grant_branch_access(user_id=user.id, branch_id=BRANCH_ID, requested_by=user.id)
        token, _ = service.authenticate(email="owner@example.com", password="s3cret!")

        auth = service.build_auth_context(token=token)  # the real thing, not a hand-built stand-in
        assert auth.user_id == user.id
        assert auth.can_access_branch(BRANCH_ID)

        analysis_lookup = InMemoryAnalysisLookup()
        analysis_lookup.register("analysis-1", AnalysisStatus.AWAITING_FILES, BRANCH_ID)

        storage = LocalEncryptedFileStorage(tmp_path / "files", Fernet.generate_key())
        import_service = ImportService(
            db=db,
            storage=storage,
            analysis_lookup=analysis_lookup,
            audit_logger=ImportAuditLogger(),
            scanner=NoOpMalwareScanner(),
        )
        record = import_service.import_file(
            analysis_id="analysis-1",
            source_type=SourceType.POS_EXPORT,
            original_filename="pos.csv",
            content=b"order_id,amount\n1,10.00\n",
            auth=auth,
        )
        assert record.uploaded_by == user.id
