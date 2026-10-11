"""
Tenancy and authorization, proven against the real composed app.

Three layers of proof:

1. ROUTE COVERAGE - every route in the OpenAPI surface has an explicit row
   in access.POLICIES (and no row is stale). A new route with no policy
   fails here, and at runtime it is denied (403 NO_ACCESS_POLICY).
2. CROSS-TENANT MATRIX - a "victim" organization B has a full pipeline
   (analysis, uploaded file, report, discrepancy, match). Every route that
   takes one of its identifiers is hit by an anonymous caller and by an
   Owner / Accountant / Auditor of a different organization. Nothing may
   succeed, and nothing may reveal that the resource exists.
3. SEMANTICS - Owner sees all own-org branches (incl. future ones), other
   roles only grants, AUDITOR is read-only, the operator sees everything,
   a client user can never become an operator, lists are filtered.

Each test gets a fresh SQLite database and a fresh import of the app.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

OPERATOR_EMAIL = "operator@midotrix.test"
OPERATOR_PW = "operator-passphrase-1"
PW = "s3cret-passphrase"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("RECONFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_EMAIL", OPERATOR_EMAIL)
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", OPERATOR_PW)
    for mod_name in list(sys.modules):
        if mod_name == "main" or mod_name.startswith("bootstrap"):
            del sys.modules[mod_name]
    import main

    with TestClient(main.app) as client:
        yield World(client, main)


class World:
    """Two client organizations (A: attackers, B: victim) plus the operator."""

    def __init__(self, client: TestClient, main_module) -> None:
        self.c = client
        self.main = main_module
        self.op = self.login(OPERATOR_EMAIL, OPERATOR_PW)

        self.org_a = self._org("Org A", "SAR")
        self.org_b = self._org("Org B", "AED")
        self.owner_a = self._user("owner-a@a.test", "OWNER", self.org_a)
        self.accountant_a = self._user("acct-a@a.test", "ACCOUNTANT", self.org_a)
        self.auditor_a = self._user("aud-a@a.test", "AUDITOR", self.org_a)
        self.owner_b = self._user("owner-b@b.test", "OWNER", self.org_b)

        self.branch_a1 = self._branch(self.org_a, "A1")
        self.branch_a2 = self._branch(self.org_a, "A2")
        self.branch_b1 = self._branch(self.org_b, "B1")

        # Owner A grants the two limited users access to A1 only.
        for u in (self.accountant_a, self.auditor_a):
            r = self.c.post(
                f"/users/{u['id']}/branch-access", json={"branch_id": self.branch_a1}, headers=self.owner_a["h"]
            )
            assert r.status_code == 201, r.text

        self.platform_id = self.c.post("/platforms", json={"name": "Talabat"}, headers=self.op).json()["id"]
        self.analysis_a1 = self._analysis(self.branch_a1)
        self.analysis_a2 = self._analysis(self.branch_a2)
        self.victim = self._victim_pipeline()

    # -- helpers -------------------------------------------------------

    def login(self, email: str, pw: str) -> dict:
        r = self.c.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def _org(self, name: str, ccy: str) -> str:
        r = self.c.post("/organizations", json={"legal_name": name, "default_currency": ccy}, headers=self.op)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def _user(self, email: str, role: str, org: str) -> dict:
        r = self.c.post(
            "/users", json={"email": email, "password": PW, "role": role, "organization_id": org}, headers=self.op
        )
        assert r.status_code == 201, r.text
        return {"id": r.json()["id"], "h": self.login(email, PW), "email": email, "org": org}

    def _branch(self, org: str, name: str) -> str:
        r = self.c.post(
            "/branches", json={"organization_id": org, "name": name, "timezone": "Asia/Riyadh"}, headers=self.op
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def _analysis(self, branch: str) -> str:
        r = self.c.post(
            "/analyses",
            json={"branch_id": branch, "period_start": "2026-03-01", "period_end": "2026-03-31"},
            headers=self.op,
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def _victim_pipeline(self) -> dict:
        """Run org B's analysis far enough that every kind of identifier exists."""
        c, h = self.c, self.op
        c.post(
            "/contracts",
            json={"branch_id": self.branch_b1, "platform_id": self.platform_id,
                  "commission_pct": "0.15", "valid_from": "2026-01-01", "valid_to": None},
            headers=h,
        )
        aid = self._analysis(self.branch_b1)
        pos = b"order_id,order_time,amount\n1001,2026-03-01 10:00:00,100.00\n1002,2026-03-02 11:00:00,80.00\n"
        plat = b"order_id,settlement_date,gross_amount,commission_amount\n1001,2026-03-01 10:00:00,100.00,15.00\n"
        ids = {}
        for st, name, content in (("POS_EXPORT", "pos.csv", pos), ("PLATFORM_SETTLEMENT", "s.csv", plat)):
            r = c.post(f"/analyses/{aid}/files", data={"source_type": st},
                       files={"file": (name, content, "text/csv")}, headers=h)
            assert r.status_code == 201, r.text
            ids[st] = r.json()["id"]
        for fid in ids.values():
            assert c.post(f"/uploaded-files/{fid}/validation", headers=h).status_code == 201
        assert c.post(f"/analyses/{aid}/mark-processing", headers=h).status_code == 200
        for fid in ids.values():
            assert c.post(f"/uploaded-files/{fid}/normalization", headers=h).status_code == 201
        for step in ("matching", "comparison", "discrepancies"):
            assert c.post(f"/analyses/{aid}/{step}", headers=h).status_code == 201, step
        assert c.post(f"/analyses/{aid}/mark-completed", headers=h).status_code == 200
        rep = c.post(f"/analyses/{aid}/reports", json={"format": "CSV"}, headers=h)
        assert rep.status_code == 201, rep.text

        victim = {
            "analysis_id": aid, "uploaded_file_id": ids["POS_EXPORT"], "report_id": rep.json()["id"],
        }
        victim.update(self._current_row_ids(aid))
        return victim

    def _current_row_ids(self, analysis_id: str) -> dict:
        """Discrepancies/matches are regenerated when the operator re-runs a
        stage, so identifiers must be re-read right before use."""
        from sqlalchemy import select
        import bootstrap.database as bdb
        from modules.discrepancies.models import Discrepancy
        from modules.matching.models import ReconciliationMatch

        with bdb.SessionLocal() as db:
            disc = db.execute(select(Discrepancy.id).where(Discrepancy.analysis_id == analysis_id)).scalars().first()
            match = db.execute(
                select(ReconciliationMatch.id).where(ReconciliationMatch.analysis_id == analysis_id)
            ).scalars().first()
        assert disc and match
        return {"discrepancy_id": disc, "reconciliation_match_id": match}

    def request_for(self, method: str, template: str, resource, *, victim: bool = True):
        """Build a request that targets org B's resources, for any policy row."""
        from bootstrap.access import R

        self.victim.update(self._current_row_ids(self.victim["analysis_id"]))
        v = self.victim
        path = template.format(
            analysis_id=v["analysis_id"], uploaded_file_id=v["uploaded_file_id"], report_id=v["report_id"],
            discrepancy_id=v["discrepancy_id"], reconciliation_match_id=v["reconciliation_match_id"],
            branch_id=self.branch_b1, organization_id=self.org_b, user_id=self.owner_b["id"],
        )
        body = None
        if resource in (R.BODY_BRANCH, R.USER_GRANT):
            body = {"branch_id": self.branch_b1}
        elif resource == R.BODY_ORG:
            body = {"organization_id": self.org_b}
        return method, path, body

    def send(self, method: str, path: str, headers=None, body=None):
        kwargs = {"headers": headers or {}}
        if body is not None:
            kwargs["json"] = body
        return self.c.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# 1. Route coverage
# ---------------------------------------------------------------------------


class TestRouteCoverage:
    def test_every_route_has_an_explicit_policy_and_no_policy_is_stale(self, world):
        from bootstrap.access import POLICIES, UNGUARDED_BY_DESIGN

        spec = world.c.get("/openapi.json").json()
        served = {
            (method.upper(), path)
            for path, ops in spec["paths"].items()
            for method in ops
        }
        covered = set(POLICIES) | set(UNGUARDED_BY_DESIGN)
        assert served - covered == set(), f"Routes with NO access policy: {sorted(served - covered)}"
        assert set(POLICIES) - served == set(), f"Stale policies: {sorted(set(POLICIES) - served)}"

    def test_unguarded_routes_are_exactly_the_reviewed_allowlist(self, world):
        from bootstrap.access import UNGUARDED_BY_DESIGN

        assert UNGUARDED_BY_DESIGN == {("GET", "/health")}

    def test_every_non_public_route_rejects_an_anonymous_caller(self, world):
        from bootstrap.access import POLICIES

        failures = []
        for (method, template), policy in POLICIES.items():
            if policy.public:
                continue
            _, path, body = world.request_for(method, template, policy.resource)
            r = world.send(method, path, headers=None, body=body)
            if r.status_code != 401:
                failures.append((method, template, r.status_code))
        assert failures == [], f"Anonymous callers got through: {failures}"

    def test_a_route_without_a_policy_row_is_denied_at_runtime(self, world, monkeypatch):
        import bootstrap.access as access

        monkeypatch.delitem(access.POLICIES, ("GET", "/platforms"))
        r = world.c.get("/platforms", headers=world.owner_a["h"])
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "NO_ACCESS_POLICY"


# ---------------------------------------------------------------------------
# 2. Cross-tenant matrix
# ---------------------------------------------------------------------------


class TestCrossTenantMatrix:
    @pytest.mark.parametrize("who", ["owner_a", "accountant_a", "auditor_a"])
    def test_other_organizations_users_get_nothing_for_any_victim_identifier(self, world, who):
        from bootstrap.access import POLICIES, R

        caller = getattr(world, who)
        leaks = []
        checked = 0
        for (method, template), policy in POLICIES.items():
            if policy.public or policy.resource == R.NONE:
                continue
            _, path, body = world.request_for(method, template, policy.resource)
            r = world.send(method, path, headers=caller["h"], body=body)
            checked += 1
            if r.status_code not in (403, 404):
                leaks.append((method, template, r.status_code))
            elif r.status_code == 404:
                # Must be the generic answer - never a module-specific code
                # that would confirm the resource exists.
                if r.json()["detail"]["error_code"] != "NOT_FOUND":
                    leaks.append((method, template, r.json()["detail"]["error_code"]))
        assert checked >= 25
        assert leaks == [], f"{who} reached org B's data: {leaks}"

    def test_operator_passes_every_policy_for_victim_identifiers(self, world):
        from bootstrap.access import POLICIES, R

        blocked = []
        for (method, template), policy in POLICIES.items():
            if policy.public or policy.resource == R.NONE:
                continue
            _, path, body = world.request_for(method, template, policy.resource)
            r = world.send(method, path, headers=world.op, body=body)
            generic_404 = r.status_code == 404 and r.json()["detail"].get("error_code") == "NOT_FOUND"
            if r.status_code in (401, 403) or generic_404:
                blocked.append((method, template, r.status_code))
        assert blocked == [], f"Operator was blocked by policy: {blocked}"

    def test_missing_and_foreign_resources_are_indistinguishable(self, world):
        mine = world.c.get(f"/analyses/{world.victim['analysis_id']}", headers=world.owner_a["h"])
        nonexistent = world.c.get("/analyses/00000000-0000-0000-0000-000000000000", headers=world.owner_a["h"])
        assert mine.status_code == nonexistent.status_code == 404
        assert mine.json() == nonexistent.json()


# ---------------------------------------------------------------------------
# 3. Semantics
# ---------------------------------------------------------------------------


class TestBrowserCompatibility:
    def test_cors_preflight_needs_no_token(self, world):
        """Browsers send an unauthenticated OPTIONS request before any call
        that carries an Authorization header; locking that out would break
        every cross-origin frontend even though the API itself is fine."""
        r = world.c.options(
            f"/analyses/{world.analysis_a1}",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "authorization" in r.headers["access-control-allow-headers"].lower()


class TestOriginalExploits:
    """The four probes from the 2026-10-07 audit, kept as permanent tests."""

    def test_open_signup_is_closed(self, world):
        r = world.c.post("/users", json={"email": "rogue@evil.test", "password": PW, "role": "OWNER"})
        assert r.status_code == 401

    def test_unauthenticated_analysis_read_is_closed(self, world):
        assert world.c.get(f"/analyses/{world.analysis_a1}").status_code == 401

    def test_unauthenticated_report_download_is_closed(self, world):
        assert world.c.get(f"/reports/{world.victim['report_id']}/download").status_code == 401

    def test_auditor_cannot_act_on_a_branch_outside_their_grant(self, world):
        r = world.c.post(
            "/analyses",
            json={"branch_id": world.branch_b1, "period_start": "2026-01-01", "period_end": "2026-01-31"},
            headers=world.auditor_a["h"],
        )
        assert r.status_code in (403, 404)

    def test_second_organization_is_now_allowed(self, world):
        assert world.org_a != world.org_b


class TestUserCreationRules:
    def test_nobody_can_create_a_platform_operator_over_http(self, world):
        for caller in (world.op, world.owner_a["h"]):
            r = world.c.post(
                "/users", json={"email": "x@x.test", "password": PW, "role": "PLATFORM_OPERATOR"}, headers=caller
            )
            assert r.status_code in (403, 422), r.text
            assert r.json()["detail"]["error_code"] in ("OPERATOR_ROLE_NOT_ASSIGNABLE", "INSUFFICIENT_ROLE")

    def test_owner_cannot_create_users_in_another_organization(self, world):
        r = world.c.post(
            "/users",
            json={"email": "spy@a.test", "password": PW, "role": "ACCOUNTANT", "organization_id": world.org_b},
            headers=world.owner_a["h"],
        )
        assert r.status_code == 404

    def test_owner_creates_users_in_their_own_organization_by_default(self, world):
        r = world.c.post(
            "/users", json={"email": "new@a.test", "password": PW, "role": "OPS_MANAGER"}, headers=world.owner_a["h"]
        )
        assert r.status_code == 201, r.text
        assert r.json()["organization_id"] == world.org_a

    def test_non_owner_roles_cannot_create_users(self, world):
        for who in (world.accountant_a, world.auditor_a):
            r = world.c.post(
                "/users", json={"email": "z@a.test", "password": PW, "role": "AUDITOR"}, headers=who["h"]
            )
            assert r.status_code == 403

    def test_client_user_requires_an_organization(self, world):
        r = world.c.post("/users", json={"email": "no-org@x.test", "password": PW, "role": "OWNER"}, headers=world.op)
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "INVALID_USER_SCOPE"

    def test_database_makes_a_client_operator_impossible(self, world):
        import bootstrap.database as bdb
        from modules.identity_access.models import User, UserRole

        with bdb.SessionLocal() as db:
            db.add(User(email="bad1@x.test", password_hash="x", role=UserRole.PLATFORM_OPERATOR, organization_id=world.org_a))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(User(email="bad2@x.test", password_hash="x", role=UserRole.OWNER, organization_id=None))
            with pytest.raises(IntegrityError):
                db.commit()


class TestBranchGrants:
    def test_grant_across_organizations_is_refused(self, world):
        h = world.owner_a["h"]
        assert world.c.post(
            f"/users/{world.accountant_a['id']}/branch-access", json={"branch_id": world.branch_b1}, headers=h
        ).status_code == 404
        assert world.c.post(
            f"/users/{world.owner_b['id']}/branch-access", json={"branch_id": world.branch_a1}, headers=h
        ).status_code == 404

    def test_grant_within_the_organization_works_and_non_owners_cannot_grant(self, world):
        assert world.c.post(
            f"/users/{world.accountant_a['id']}/branch-access", json={"branch_id": world.branch_a2},
            headers=world.owner_a["h"],
        ).status_code == 201
        assert world.c.post(
            f"/users/{world.accountant_a['id']}/branch-access", json={"branch_id": world.branch_a2},
            headers=world.accountant_a["h"],
        ).status_code == 403

    def test_operator_cannot_be_granted_branch_access(self, world):
        import bootstrap.database as bdb
        from modules.identity_access.models import User

        with bdb.SessionLocal() as db:
            op_id = db.query(User).filter(User.email == OPERATOR_EMAIL).one().id
        r = world.c.post(f"/users/{op_id}/branch-access", json={"branch_id": world.branch_a1}, headers=world.owner_a["h"])
        assert r.status_code == 404


class TestVisibility:
    def test_owner_sees_every_branch_of_their_org_including_future_ones(self, world):
        h = world.owner_a["h"]
        ids = {b["id"] for b in world.c.get("/auth/me/branches", headers=h).json()}
        assert ids == {world.branch_a1, world.branch_a2}

        new_branch = world.c.post(
            "/branches", json={"organization_id": world.org_a, "name": "A3", "timezone": "Asia/Riyadh"}, headers=h
        )
        assert new_branch.status_code == 201
        ids = {b["id"] for b in world.c.get("/auth/me/branches", headers=h).json()}
        assert new_branch.json()["id"] in ids  # no grant needed
        assert world.c.post(
            "/analyses",
            json={"branch_id": new_branch.json()["id"], "period_start": "2026-04-01", "period_end": "2026-04-30"},
            headers=h,
        ).status_code == 201

    def test_other_roles_see_only_their_grants(self, world):
        for who in (world.accountant_a, world.auditor_a):
            ids = {b["id"] for b in world.c.get("/auth/me/branches", headers=who["h"]).json()}
            assert ids == {world.branch_a1}
            assert world.c.get(f"/analyses/{world.analysis_a1}", headers=who["h"]).status_code == 200
            assert world.c.get(f"/analyses/{world.analysis_a2}", headers=who["h"]).status_code == 404

    def test_a_new_branch_is_invisible_to_limited_roles_until_granted(self, world):
        new = world.c.post(
            "/branches", json={"organization_id": world.org_a, "name": "A3", "timezone": "Asia/Riyadh"},
            headers=world.owner_a["h"],
        ).json()["id"]
        ids = {b["id"] for b in world.c.get("/auth/me/branches", headers=world.accountant_a["h"]).json()}
        assert new not in ids

    def test_operator_sees_all_branches_across_organizations(self, world):
        ids = {b["id"] for b in world.c.get("/auth/me/branches", headers=world.op).json()}
        assert {world.branch_a1, world.branch_a2, world.branch_b1} <= ids

    def test_organizations_listing_is_scoped(self, world):
        op_orgs = {o["id"] for o in world.c.get("/organizations", headers=world.op).json()}
        assert op_orgs == {world.org_a, world.org_b}
        assert [o["id"] for o in world.c.get("/organizations", headers=world.owner_a["h"]).json()] == [world.org_a]
        assert world.c.get("/organizations/current", headers=world.owner_a["h"]).json()["id"] == world.org_a
        assert world.c.get("/organizations/current", headers=world.op).json() is None

    def test_branch_listing_is_scoped_by_organization_and_grants(self, world):
        assert world.c.get(f"/organizations/{world.org_b}/branches", headers=world.owner_a["h"]).status_code == 404
        owner_view = {b["id"] for b in world.c.get(f"/organizations/{world.org_a}/branches", headers=world.owner_a["h"]).json()}
        acct_view = {b["id"] for b in world.c.get(f"/organizations/{world.org_a}/branches", headers=world.accountant_a["h"]).json()}
        assert owner_view == {world.branch_a1, world.branch_a2}
        assert acct_view == {world.branch_a1}


class TestRoleGates:
    def test_auditor_is_read_only(self, world):
        h = world.auditor_a["h"]
        assert world.c.get(f"/analyses/{world.analysis_a1}", headers=h).status_code == 200
        assert world.c.get(f"/analyses/{world.analysis_a1}/summary", headers=h).status_code != 403
        for path in ("mark-processing", "matching", "comparison", "discrepancies", "mark-failed"):
            r = world.c.post(f"/analyses/{world.analysis_a1}/{path}", headers=h, json={"reason": "x"})
            assert r.status_code == 403, (path, r.status_code)
        assert world.c.post(
            "/analyses", json={"branch_id": world.branch_a1, "period_start": "2026-05-01", "period_end": "2026-05-31"},
            headers=h,
        ).status_code == 403

    def test_accountant_can_work_but_not_administer(self, world):
        h = world.accountant_a["h"]
        assert world.c.post(
            "/analyses", json={"branch_id": world.branch_a1, "period_start": "2026-05-01", "period_end": "2026-05-31"},
            headers=h,
        ).status_code == 201
        assert world.c.post(
            "/branches", json={"organization_id": world.org_a, "name": "X", "timezone": "Asia/Riyadh"}, headers=h
        ).status_code == 403
        assert world.c.post(
            "/contracts",
            json={"branch_id": world.branch_a1, "platform_id": world.platform_id,
                  "commission_pct": "0.2", "valid_from": "2026-01-01", "valid_to": None},
            headers=h,
        ).status_code == 403

    def test_platform_and_organization_creation_are_operator_only(self, world):
        h = world.owner_a["h"]
        assert world.c.post("/platforms", json={"name": "Jahez"}, headers=h).status_code == 403
        assert world.c.post("/organizations", json={"legal_name": "Z", "default_currency": "SAR"}, headers=h).status_code == 403
        assert world.c.post("/platforms", json={"name": "Jahez"}, headers=world.op).status_code == 201

    def test_deployment_endpoints_are_operator_only(self, world):
        for method, path in (("GET", "/deployment/info"), ("GET", "/deployment/updates")):
            assert world.c.request(method, path, headers=world.owner_a["h"]).status_code == 403
            assert world.c.request(method, path, headers=world.op).status_code == 200

    def test_branch_creation_in_another_organization_is_refused(self, world):
        r = world.c.post(
            "/branches", json={"organization_id": world.org_b, "name": "Sneaky", "timezone": "Asia/Riyadh"},
            headers=world.owner_a["h"],
        )
        assert r.status_code == 404


class TestAuditLogScoping:
    def test_owner_sees_only_their_own_organizations_audit_entries(self, world):
        mine = world.c.get("/audit-log?limit=1000", headers=world.owner_a["h"]).json()
        assert mine, "expected audit entries for org A"
        everything = world.c.get("/audit-log?limit=1000", headers=world.op).json()
        victim_entries = [e for e in everything if e["analysis_id"] == world.victim["analysis_id"]]
        assert victim_entries, "operator should see org B's analysis events"
        assert len(mine) < len(everything)
        assert not [e for e in mine if e["analysis_id"] == world.victim["analysis_id"]]
        # filtering by a victim analysis id must not bypass the org scope
        probe = world.c.get(f"/audit-log?analysis_id={world.victim['analysis_id']}", headers=world.owner_a["h"])
        assert probe.status_code == 200 and probe.json() == []

    def test_non_owner_roles_cannot_read_the_audit_log(self, world):
        for who in (world.accountant_a, world.auditor_a):
            assert world.c.get("/audit-log", headers=who["h"]).status_code == 403


class TestListUsers:
    def test_operator_sees_everyone_and_an_owner_only_their_own_organization(self, world):
        everyone = {u["email"] for u in world.c.get("/users", headers=world.op).json()}
        assert {OPERATOR_EMAIL, world.owner_a["email"], world.accountant_a["email"],
                world.auditor_a["email"], world.owner_b["email"]} <= everyone

        mine = world.c.get("/users", headers=world.owner_a["h"]).json()
        assert {u["email"] for u in mine} == {world.owner_a["email"], world.accountant_a["email"], world.auditor_a["email"]}
        assert all(u["organization_id"] == world.org_a for u in mine)
        assert not any("password" in key for u in mine for key in u)  # no hashes in the response

    def test_other_roles_and_anonymous_cannot_list(self, world):
        assert world.c.get("/users").status_code == 401
        for who in (world.accountant_a, world.auditor_a):
            assert world.c.get("/users", headers=who["h"]).status_code == 403


class TestPasswordChange:
    NEW = "a-brand-new-passphrase-1"

    def _login_code(self, world, email, pw):
        return world.c.post("/auth/login", json={"email": email, "password": pw}).status_code

    def test_operator_resets_a_users_password_and_their_old_session_dies(self, world):
        old_headers = world.owner_a["h"]
        assert world.c.get("/auth/me", headers=old_headers).status_code == 200
        r = world.c.post(f"/users/{world.owner_a['id']}/password", json={"new_password": self.NEW}, headers=world.op)
        assert r.status_code == 204, r.text
        assert world.c.get("/auth/me", headers=old_headers).status_code == 401  # old token revoked
        assert self._login_code(world, world.owner_a["email"], PW) == 401       # old password dead
        assert self._login_code(world, world.owner_a["email"], self.NEW) == 200  # new one works

    def test_owner_can_reset_users_of_their_own_organization_only(self, world):
        h = world.owner_a["h"]
        assert world.c.post(f"/users/{world.accountant_a['id']}/password", json={"new_password": self.NEW}, headers=h).status_code == 204
        assert self._login_code(world, world.accountant_a["email"], self.NEW) == 200
        # another organization's user, and the operator: indistinguishable from "no such user"
        for target in (world.owner_b["id"], world.victim["analysis_id"]):
            r = world.c.post(f"/users/{target}/password", json={"new_password": self.NEW}, headers=h)
            assert r.status_code == 404 and r.json()["detail"]["error_code"] == "NOT_FOUND"
        import bootstrap.database as bdb
        from modules.identity_access.models import User

        with bdb.SessionLocal() as db:
            op_id = db.query(User).filter(User.email == OPERATOR_EMAIL).one().id
        assert world.c.post(f"/users/{op_id}/password", json={"new_password": self.NEW}, headers=h).status_code == 404
        assert self._login_code(world, OPERATOR_EMAIL, OPERATOR_PW) == 200  # untouched

    def test_non_owners_cannot_reset_other_users(self, world):
        for who in (world.accountant_a, world.auditor_a):
            r = world.c.post(f"/users/{world.owner_a['id']}/password", json={"new_password": self.NEW}, headers=who["h"])
            assert r.status_code == 404

    def test_changing_your_own_password_needs_the_current_one(self, world):
        me = world.accountant_a
        url = f"/users/{me['id']}/password"
        assert world.c.post(url, json={"new_password": self.NEW}, headers=me["h"]).status_code == 403
        wrong = world.c.post(url, json={"new_password": self.NEW, "current_password": "not-it-at-all"}, headers=me["h"])
        assert wrong.status_code == 403 and wrong.json()["detail"]["error_code"] == "CURRENT_PASSWORD_INCORRECT"
        assert world.c.post(url, json={"new_password": self.NEW, "current_password": PW}, headers=me["h"]).status_code == 204
        assert self._login_code(world, me["email"], self.NEW) == 200
        assert self._login_code(world, me["email"], PW) == 401

    def test_weak_or_unchanged_passwords_and_anonymous_callers_are_refused(self, world):
        url = f"/users/{world.accountant_a['id']}/password"
        assert world.c.post(url, json={"new_password": "short"}, headers=world.op).status_code == 422
        same = world.c.post(url, json={"new_password": PW}, headers=world.op)
        assert same.status_code == 422 and same.json()["detail"]["error_code"] == "PASSWORD_UNCHANGED"
        assert world.c.post(url, json={"new_password": self.NEW}).status_code == 401

    def test_the_change_is_audited_without_the_password(self, world):
        world.c.post(f"/users/{world.accountant_a['id']}/password", json={"new_password": self.NEW}, headers=world.owner_a["h"])
        entries = [e for e in world.c.get("/audit-log?limit=1000&event=password_changed", headers=world.op).json()]
        assert len(entries) == 1
        assert self.NEW not in str(entries[0]) and entries[0]["details"]["target_user_id"] == world.accountant_a["id"]
