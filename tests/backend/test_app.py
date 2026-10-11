"""
End-to-end test of the fully composed application.

Unlike every module's own unit tests (which call services directly),
this test goes through real HTTP requests against the actual main.app,
with bootstrap/wiring.py's real dependency overrides in effect - proving
the *composition*, not just each module in isolation. It walks nearly the
entire pipeline: seed the platform operator, onboard an organization and its Owner,
configure a contract, create an analysis, upload and validate two files,
normalize them, run matching/comparison/discrepancy detection, generate
an AI insight and a report, send a notification, and confirm the audit
trail and deployment info are both real and queryable at the end.

Each test using the `client` fixture gets a fresh temp SQLite database and
storage directory, and a fresh import of main/bootstrap, so tests don't
leak state into each other despite bootstrap/wiring.py's module-level
singletons.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECONFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    # The platform operator is the only account that can exist without an
    # organization; it is created by the startup seed (no HTTP route).
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_EMAIL", "operator@midotrix.test")
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", "operator-passphrase-1")

    for mod_name in list(sys.modules):
        if mod_name == "main" or mod_name.startswith("bootstrap"):
            del sys.modules[mod_name]

    import main

    with TestClient(main.app) as test_client:
        yield test_client


class TestOpenAPISchema:
    def test_every_module_route_is_registered(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        expected = [
            "/users", "/auth/login", "/auth/logout", "/auth/me", "/auth/me/branches",
            "/users/{user_id}/branch-access",
            "/organizations", "/organizations/current", "/branches",
            "/branches/{branch_id}", "/organizations/{organization_id}/branches",
            "/platforms", "/contracts", "/branches/{branch_id}/contracts",
            "/audit-log",
            "/deployment/info", "/deployment/license", "/deployment/updates",
            "/analyses", "/analyses/{analysis_id}",
            "/analyses/{analysis_id}/files",
            "/uploaded-files/{uploaded_file_id}/validation",
            "/uploaded-files/{uploaded_file_id}/normalization",
            "/analyses/{analysis_id}/matching",
            "/analyses/{analysis_id}/comparison",
            "/analyses/{analysis_id}/discrepancies",
            "/matches/{reconciliation_match_id}/review",
            "/discrepancies/{discrepancy_id}/review",
            "/analyses/{analysis_id}/manual-match",
            "/analyses/{analysis_id}/ai-insights",
            "/analyses/{analysis_id}/versions",
            "/analyses/{analysis_id}/mark-processing",
            "/analyses/{analysis_id}/mark-completed",
            "/analyses/{analysis_id}/mark-failed",
            "/analyses/{analysis_id}/reports", "/reports/{report_id}/download",
            "/analyses/{analysis_id}/notifications",
        ]
        missing = [p for p in expected if p not in paths]
        assert missing == [], f"Routes missing from the composed app: {missing}"

    def test_health_check_responds(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestFullPipelineThroughRealHttp:
    def test_end_to_end_flow(self, client):
        # 1. The seeded platform operator signs in (no HTTP route can create one).
        op_login = client.post(
            "/auth/login",
            json={"email": "operator@midotrix.test", "password": "operator-passphrase-1"},
        )
        assert op_login.status_code == 200, op_login.text
        assert op_login.json()["user"]["role"] == "PLATFORM_OPERATOR"
        assert op_login.json()["user"]["organization_id"] is None
        op_auth = {"Authorization": f"Bearer {op_login.json()['token']}"}

        # 2. The operator onboards a client: organization, then its Owner.
        org_resp = client.post(
            "/organizations", json={"legal_name": "Acme Restaurants", "default_currency": "SAR"},
            headers=op_auth,
        )
        assert org_resp.status_code == 201, org_resp.text
        org_id = org_resp.json()["id"]

        create_user_resp = client.post(
            "/users",
            json={"email": "owner@example.com", "password": "s3cret!", "role": "OWNER",
                  "organization_id": org_id},
            headers=op_auth,
        )
        assert create_user_resp.status_code == 201, create_user_resp.text
        user_id = create_user_resp.json()["id"]
        assert create_user_resp.json()["organization_id"] == org_id

        # 3. The client's Owner logs in and works inside their own organization.
        login_resp = client.post(
            "/auth/login", json={"email": "owner@example.com", "password": "s3cret!"}
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        me_resp = client.get("/auth/me", headers=auth)
        assert me_resp.status_code == 200
        assert me_resp.json()["id"] == user_id

        branch_resp = client.post(
            "/branches", json={"organization_id": org_id, "name": "Downtown", "timezone": "Asia/Riyadh"},
            headers=auth,
        )
        assert branch_resp.status_code == 201, branch_resp.text
        branch_id = branch_resp.json()["id"]

        # An Owner sees every branch of their organization with no grants.
        my_branches = client.get("/auth/me/branches", headers=auth)
        assert my_branches.status_code == 200
        assert [b["id"] for b in my_branches.json()] == [branch_id]

        # 4. Reference & contract configuration.
        platform_resp = client.post("/platforms", json={"name": "Talabat"}, headers=op_auth)
        assert platform_resp.status_code == 201, platform_resp.text
        platform_id = platform_resp.json()["id"]

        contract_resp = client.post(
            "/contracts",
            json={
                "branch_id": branch_id, "platform_id": platform_id,
                "commission_pct": "0.15", "valid_from": "2026-01-01", "valid_to": None,
            },
            headers=auth,
        )
        assert contract_resp.status_code == 201, contract_resp.text

        # 5. Create the analysis.
        analysis_resp = client.post(
            "/analyses",
            json={"branch_id": branch_id, "period_start": "2026-03-01", "period_end": "2026-03-31"},
            headers=auth,
        )
        assert analysis_resp.status_code == 201, analysis_resp.text
        analysis_id = analysis_resp.json()["id"]
        assert analysis_resp.json()["status"] == "AWAITING_FILES"

        # 6. Upload both files - a matched pair, exact commission.
        pos_content = b"order_id,order_time,amount\n1001,2026-03-01 10:00:00,100.00\n"
        platform_content = (
            b"order_id,settlement_date,gross_amount,commission_amount\n"
            b"1001,2026-03-01 10:00:00,100.00,15.00\n"
        )

        pos_upload_resp = client.post(
            f"/analyses/{analysis_id}/files",
            data={"source_type": "POS_EXPORT"},
            files={"file": ("pos.csv", pos_content, "text/csv")},
            headers=auth,
        )
        assert pos_upload_resp.status_code == 201, pos_upload_resp.text
        pos_file_id = pos_upload_resp.json()["id"]

        platform_upload_resp = client.post(
            f"/analyses/{analysis_id}/files",
            data={"source_type": "PLATFORM_SETTLEMENT"},
            files={"file": ("settlement.csv", platform_content, "text/csv")},
            headers=auth,
        )
        assert platform_upload_resp.status_code == 201, platform_upload_resp.text
        platform_file_id = platform_upload_resp.json()["id"]

        # 7. Validate both.
        for file_id in (pos_file_id, platform_file_id):
            validation_resp = client.post(f"/uploaded-files/{file_id}/validation", headers=auth)
            assert validation_resp.status_code == 201, validation_resp.text
            assert validation_resp.json()["status"] == "PASSED"

        # 8. Mark the analysis as processing - the async stage begins.
        processing_resp = client.post(f"/analyses/{analysis_id}/mark-processing", headers=auth)
        assert processing_resp.status_code == 200, processing_resp.text
        assert processing_resp.json()["status"] == "PROCESSING"

        # 9. Normalize both files.
        for file_id in (pos_file_id, platform_file_id):
            normalize_resp = client.post(f"/uploaded-files/{file_id}/normalization", headers=auth)
            assert normalize_resp.status_code == 201, normalize_resp.text
            assert normalize_resp.json()["rows_created"] == 1

        # 10. Matching, comparison, discrepancy detection.
        matching_resp = client.post(f"/analyses/{analysis_id}/matching", headers=auth)
        assert matching_resp.status_code == 201, matching_resp.text
        assert matching_resp.json()["matched_count"] == 1

        comparison_resp = client.post(f"/analyses/{analysis_id}/comparison", headers=auth)
        assert comparison_resp.status_code == 201, comparison_resp.text
        assert comparison_resp.json()["compared_count"] == 1
        assert comparison_resp.json()["within_tolerance_count"] == 1

        discrepancies_resp = client.post(f"/analyses/{analysis_id}/discrepancies", headers=auth)
        assert discrepancies_resp.status_code == 201, discrepancies_resp.text
        assert discrepancies_resp.json()["total_count"] == 0  # a clean, fully-reconciled pair

        # 11. AI insight (FakeAIProvider, since no ANTHROPIC_API_KEY in this test env).
        insight_resp = client.post(f"/analyses/{analysis_id}/ai-insights", headers=auth)
        assert insight_resp.status_code == 201, insight_resp.text
        assert insight_resp.json()["provider_name"] == "fake"

        # 12. Mark completed.
        completed_resp = client.post(f"/analyses/{analysis_id}/mark-completed", headers=auth)
        assert completed_resp.status_code == 200, completed_resp.text
        assert completed_resp.json()["status"] == "COMPLETED"

        # 13. Generate and download a report.
        report_resp = client.post(
            f"/analyses/{analysis_id}/reports", json={"format": "CSV"}, headers=auth
        )
        assert report_resp.status_code == 201, report_resp.text
        report_id = report_resp.json()["id"]

        assert client.get(f"/reports/{report_id}/download").status_code == 401
        download_resp = client.get(f"/reports/{report_id}/download", headers=auth)
        assert download_resp.status_code == 200
        assert download_resp.headers["content-type"].startswith("text/csv")

        # 14. Send a notification (FakeNotificationChannel, since no SMTP config).
        notify_resp = client.post(
            f"/analyses/{analysis_id}/notifications",
            json={"event_type": "ANALYSIS_COMPLETED", "recipient": "owner@example.com"},
            headers=auth,
        )
        assert notify_resp.status_code == 201, notify_resp.text
        assert notify_resp.json()["status"] == "SENT"

        # 15. The audit trail should have real entries from everything above.
        audit_resp = client.get(f"/audit-log?analysis_id={analysis_id}", headers=auth)
        assert audit_resp.status_code == 200
        events = {entry["event"] for entry in audit_resp.json()}
        assert "file_import_succeeded" in events
        assert "matching_run_completed" in events
        assert "comparison_run_completed" in events
        assert "ai_insight_generated" in events
        assert "report_generated" in events
        assert "notification_attempted" in events

        # 16. Deployment info is platform-operator-only.
        assert client.get("/deployment/info").status_code == 401
        assert client.get("/deployment/info", headers=auth).status_code == 403
        deployment_resp = client.get("/deployment/info", headers=op_auth)
        assert deployment_resp.status_code == 200
        assert deployment_resp.json()["license_status"] == "UNLICENSED"
