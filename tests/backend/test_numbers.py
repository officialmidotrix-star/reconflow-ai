"""
The headline number, end to end through the real HTTP API, with arithmetic
worked out by hand.

Commission contract: 15%. POS (SAR) and the platform's settlement file:

  ref  POS    status               platform gross / commission     what should happen
  1    100    -                    100 / 15.00                     clean match
  2    200    -                    (no settlement)                 MISSING      -> recoverable 200 x 0.85 = 170.00
  3     80    Cancelled by cust.   (no settlement)                 CANCELLED    -> needs confirmation (not claimed yet)
  4     50    Refunded             (no settlement)                 EXCLUDED     -> no payout expected; counted, not claimed
  5    100    -                     90 / 13.50                     UNDERPAID 10 -> recoverable 10 x 0.85 = 8.50
                                                                  (commission 13.50 vs 15.00 is in the restaurant's favour: to verify)
  6    100    -                    103 / 15.45                     OVERPAID 3   -> to verify, NOT a loss
  7    100    -                    100 / 20.00                     commission 5 too high -> recoverable 5.00
  99   -      -                     55 / 8.25  (no POS order)      UNEXPECTED   -> to verify, NOT a loss

  Headline = 170.00 + 8.50 + 5.00 = 183.50
  (the old definition summed everything: 200+80+10+3+5+55 = 353 and counted overpayments and unexpected money as loss)
"""

from __future__ import annotations

import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

OPERATOR = ("operator@midotrix.test", "operator-passphrase-1")
PW = "s3cret-passphrase"

POS = (
    "order_id,order_time,amount,order_status\n"
    "1,2026-03-02 10:00:00,100.00,\n"
    "2,2026-03-02 11:00:00,200.00,\n"
    "3,2026-03-03 12:00:00,80.00,Cancelled by customer\n"
    "4,2026-03-03 13:00:00,50.00,Refunded\n"
    "5,2026-03-04 14:00:00,100.00,\n"
    "6,2026-03-04 15:00:00,100.00,\n"
    "7,2026-03-05 16:00:00,100.00,\n"
).encode()
PLATFORM = (
    "order_id,settlement_date,gross_amount,commission_amount\n"
    "1,2026-03-02 10:00:00,100.00,15.00\n"
    "5,2026-03-04 14:00:00,90.00,13.50\n"
    "6,2026-03-04 15:00:00,103.00,15.45\n"
    "7,2026-03-05 16:00:00,100.00,20.00\n"
    "99,2026-03-06 09:00:00,55.00,8.25\n"
).encode()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RECONFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/n.db")
    monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_EMAIL", OPERATOR[0])
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", OPERATOR[1])
    for m in list(sys.modules):
        if m == "main" or m.startswith("bootstrap"):
            del sys.modules[m]
    import main

    with TestClient(main.app) as c:
        yield Api(c)


class Api:
    def __init__(self, c: TestClient) -> None:
        self.c = c
        r = c.post("/auth/login", json={"email": OPERATOR[0], "password": OPERATOR[1]})
        self.op = {"Authorization": f"Bearer {r.json()['token']}"}
        self.org = c.post("/organizations", json={"legal_name": "Acme", "default_currency": "SAR"}, headers=self.op).json()["id"]
        r = c.post("/users", json={"email": "owner@acme.test", "password": PW, "role": "OWNER", "organization_id": self.org}, headers=self.op)
        assert r.status_code == 201, r.text
        tok = c.post("/auth/login", json={"email": "owner@acme.test", "password": PW}).json()["token"]
        self.owner = {"Authorization": f"Bearer {tok}"}
        self.branch = c.post("/branches", json={"organization_id": self.org, "name": "B", "timezone": "Asia/Riyadh"}, headers=self.owner).json()["id"]
        self.platform = c.post("/platforms", json={"name": "Talabat"}, headers=self.op).json()["id"]

    def add_contract(self, pct="0.15"):
        r = self.c.post("/contracts", json={"branch_id": self.branch, "platform_id": self.platform, "commission_pct": pct,
                                            "valid_from": "2026-01-01", "valid_to": None}, headers=self.owner)
        assert r.status_code == 201, r.text

    def run(self) -> str:
        c, h = self.c, self.owner
        aid = c.post("/analyses", json={"branch_id": self.branch, "period_start": "2026-03-01", "period_end": "2026-03-31"}, headers=h).json()["id"]
        ids = []
        for st, name, content in (("POS_EXPORT", "pos.csv", POS), ("PLATFORM_SETTLEMENT", "s.csv", PLATFORM)):
            r = c.post(f"/analyses/{aid}/files", data={"source_type": st}, files={"file": (name, content, "text/csv")}, headers=h)
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        for f in ids:
            assert c.post(f"/uploaded-files/{f}/validation", headers=h).status_code == 201
        c.post(f"/analyses/{aid}/mark-processing", headers=h)
        for f in ids:
            assert c.post(f"/uploaded-files/{f}/normalization", headers=h).status_code == 201
        for step in ("matching", "comparison", "discrepancies"):
            assert c.post(f"/analyses/{aid}/{step}", headers=h).status_code == 201, step
        c.post(f"/analyses/{aid}/mark-completed", headers=h)
        return aid

    def summary(self, aid):
        return self.c.get(f"/analyses/{aid}/summary", headers=self.owner).json()

    def rows(self, aid):
        return self.c.get(f"/analyses/{aid}/discrepancies", headers=self.owner).json()


def D(x) -> Decimal:
    return Decimal(str(x))


class TestHeadline:
    def test_hand_calculated_numbers(self, api):
        api.add_contract()
        aid = api.run()
        s = api.summary(aid)
        g = s["groups"]

        assert D(s["total_recoverable_amount"]) == D("183.50")
        assert D(s["total_potential_revenue_leakage"]) == D("183.50")   # deprecated alias, same number
        assert (g["recoverable"]["count"], D(g["recoverable"]["amount"])) == (3, D("183.50"))
        assert (g["needs_confirmation"]["count"], D(g["needs_confirmation"]["amount"])) == (1, D("80.00"))
        assert g["needs_contract"]["count"] == 0 and g["not_claimable"]["count"] == 0 and g["needs_rerun"]["count"] == 0
        assert s["excluded_refund_void_count"] == 1          # order 4: refunded, no payout expected
        assert s["orders_not_checked_no_contract"] == 0

        rows = {r["order_reference"] + ":" + r["category"]: r for r in api.rows(aid)}
        assert D(rows["2:MISSING_SETTLEMENT"]["claimable_amount"]) == D("170.00")
        assert D(rows["5:SETTLEMENT_AMOUNT_MISMATCH"]["claimable_amount"]) == D("8.50")
        assert rows["5:SETTLEMENT_AMOUNT_MISMATCH"]["direction"] == "UNDERPAID"
        assert D(rows["7:INCORRECT_COMMISSION"]["claimable_amount"]) == D("5.00")
        assert rows["6:SETTLEMENT_AMOUNT_MISMATCH"]["treatment"] == "TO_VERIFY"     # overpaid by 3
        assert rows["6:SETTLEMENT_AMOUNT_MISMATCH"]["direction"] == "OVERPAID"
        assert rows["99:UNEXPECTED_SETTLEMENT"]["treatment"] == "TO_VERIFY"
        assert rows["3:CANCELLED_AFTER_PREPARATION"]["treatment"] == "NEEDS_CONFIRMATION"
        assert "4:MISSING_SETTLEMENT" not in rows and "4:CANCELLED_AFTER_PREPARATION" not in rows

        # nothing that is not a loss is in the headline: to-verify money is shown, separately
        assert g["to_verify"]["count"] >= 2
        # the OLD definition would have said 353 (everything summed, overpayments and unexpected money included)
        assert D(s["total_recoverable_amount"]) != D("353.00")

    def test_confirming_who_cancelled_moves_the_headline_by_exactly_the_net_amount(self, api):
        api.add_contract()
        aid = api.run()
        cancelled = next(r for r in api.rows(aid) if r["category"] == "CANCELLED_AFTER_PREPARATION")

        r = api.c.post(f"/discrepancies/{cancelled['id']}/cancellation", json={"cancelled_by": "PLATFORM"}, headers=api.owner)
        assert r.status_code == 200, r.text
        assert (r.json()["treatment"], D(r.json()["claimable_amount"])) == ("RECOVERABLE", D("68.00"))  # 80 x 0.85
        assert D(api.summary(aid)["total_recoverable_amount"]) == D("251.50")                            # 183.50 + 68.00

        api.c.post(f"/discrepancies/{cancelled['id']}/cancellation", json={"cancelled_by": "RESTAURANT"}, headers=api.owner)
        s = api.summary(aid)
        assert D(s["total_recoverable_amount"]) == D("183.50")
        assert (s["groups"]["not_claimable"]["count"], D(s["groups"]["not_claimable"]["amount"])) == (1, D("80.00"))

    def test_a_confirmation_survives_running_the_analysis_again(self, api):
        api.add_contract()
        aid = api.run()
        cancelled = next(r for r in api.rows(aid) if r["category"] == "CANCELLED_AFTER_PREPARATION")
        api.c.post(f"/discrepancies/{cancelled['id']}/cancellation", json={"cancelled_by": "CUSTOMER"}, headers=api.owner)
        assert api.c.post(f"/analyses/{aid}/discrepancies", headers=api.owner).status_code == 201   # re-run
        assert D(api.summary(aid)["total_recoverable_amount"]) == D("251.50")

    def test_only_a_cancellation_can_be_confirmed(self, api):
        api.add_contract()
        aid = api.run()
        missing = next(r for r in api.rows(aid) if r["category"] == "MISSING_SETTLEMENT")
        r = api.c.post(f"/discrepancies/{missing['id']}/cancellation", json={"cancelled_by": "PLATFORM"}, headers=api.owner)
        assert r.status_code == 422 and r.json()["detail"]["error_code"] == "NOT_A_CANCELLATION"


class TestNoContract:
    """The case that used to fail silently: no commission contract."""

    def test_nothing_is_guessed_and_the_skip_is_disclosed(self, api):
        aid = api.run()  # no contract added
        s = api.summary(aid)
        assert D(s["total_recoverable_amount"]) == D("0")                  # nothing can be quantified net of commission
        assert s["orders_not_checked_no_contract"] == 4                    # the 4 matched pairs were NOT checked - and we say so
        assert s["groups"]["needs_contract"]["count"] == 1                 # order 2 (missing) needs a rate to be quantified
        assert D(s["groups"]["needs_contract"]["amount"]) == D("200.00")  # shown gross, clearly not claimable yet
        rows = api.rows(aid)
        assert not [r for r in rows if r["category"] in ("SETTLEMENT_AMOUNT_MISMATCH", "INCORRECT_COMMISSION")]
        assert all(r["claimable_amount"] is None for r in rows)

    def test_adding_the_contract_and_running_again_quantifies_everything(self, api):
        aid = api.run()
        assert D(api.summary(aid)["total_recoverable_amount"]) == D("0")
        api.add_contract()
        for step in ("comparison", "discrepancies"):
            api.c.post(f"/analyses/{aid}/{step}", headers=api.owner)
        s = api.summary(aid)
        assert D(s["total_recoverable_amount"]) == D("183.50") and s["orders_not_checked_no_contract"] == 0


class TestAmbiguousContract:
    def test_a_branch_on_two_platforms_is_not_silently_guessed(self, api):
        """Until analyses are scoped to a branch+platform pair (Batch 4), a branch
        with contracts on two platforms has no single rate: say so, never pick one."""
        api.add_contract("0.15")
        jahez = api.c.post("/platforms", json={"name": "Jahez"}, headers=api.op).json()["id"]
        r = api.c.post("/contracts", json={"branch_id": api.branch, "platform_id": jahez, "commission_pct": "0.20",
                                           "valid_from": "2026-01-01", "valid_to": None}, headers=api.owner)
        assert r.status_code == 201, r.text
        s = api.summary(api.run())
        assert D(s["total_recoverable_amount"]) == D("0")
        assert s["orders_not_checked_no_contract"] == 4


class TestDemoDataAndSampleFiles:
    """The labelled test data + sample files must give exactly the numbers docs/sample_files/README.md promises.

    Talabat test branch, 20% test rate, worked out by hand:
      order 2 missing 200 -> 200 x 0.80 = 160.00 | order 5 underpaid 10 -> 10 x 0.80 = 8.00 | order 7 commission 5 too high -> 5.00
      headline 173.00 | order 3 cancelled (80.00) waits for 'who cancelled?' | 80 x 0.80 = 64.00 once confirmed -> 237.00
    """

    @pytest.fixture()
    def demo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RECONFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/d.db")
        monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "files"))
        monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_EMAIL", OPERATOR[0])
        monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", OPERATOR[1])
        monkeypatch.setenv("RECONFLOW_SEED_DEMO_DATA", "1")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        for m in list(sys.modules):
            if m == "main" or m.startswith("bootstrap"):
                del sys.modules[m]
        import main

        with TestClient(main.app) as c:
            tok = c.post("/auth/login", json={"email": OPERATOR[0], "password": OPERATOR[1]}).json()["token"]
            yield c, {"Authorization": f"Bearer {tok}"}, main

    def test_the_test_data_is_clearly_labelled_and_created_once(self, demo):
        c, h, main = demo
        orgs = c.get("/organizations", headers=h).json()
        assert [o["legal_name"] for o in orgs] == ["TEST DATA - Demo Restaurants (not a real client)"]
        branches = c.get("/auth/me/branches", headers=h).json()
        assert len(branches) == 3 and all(b["name"].startswith("TEST ") and "test rate" in b["name"] for b in branches)
        from bootstrap.seed_demo import run_demo_seed

        run_demo_seed()  # a second start must not duplicate anything
        assert len(c.get("/organizations", headers=h).json()) == 1 and len(c.get("/auth/me/branches", headers=h).json()) == 3
        rates = {}
        for b in branches:
            rates[b["name"].split(" - ")[1].split(" (")[0]] = {Decimal(x["commission_pct"]) for x in c.get(f"/branches/{b['id']}/contracts", headers=h).json()}
        assert rates == {"Talabat": {Decimal("0.2")}, "Jahez": {Decimal("0.15")}, "HungerStation": {Decimal("0.3")}}

    def test_sample_files_give_the_documented_numbers(self, demo):
        from pathlib import Path

        c, h, _ = demo
        folder = Path(__file__).resolve().parents[2] / "docs" / "sample_files"
        branch = next(b for b in c.get("/auth/me/branches", headers=h).json() if "Talabat" in b["name"])["id"]
        aid = c.post("/analyses", json={"branch_id": branch, "period_start": "2026-03-01", "period_end": "2026-03-31"}, headers=h).json()["id"]
        ids = []
        for st, fname in (("POS_EXPORT", "TEST_pos_export.csv"), ("PLATFORM_SETTLEMENT", "TEST_talabat_settlement.csv")):
            r = c.post(f"/analyses/{aid}/files", data={"source_type": st},
                       files={"file": (fname, (folder / fname).read_bytes(), "text/csv")}, headers=h)
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        for f in ids:
            assert c.post(f"/uploaded-files/{f}/validation", headers=h).json()["status"] == "PASSED"
        c.post(f"/analyses/{aid}/mark-processing", headers=h)
        for f in ids:
            c.post(f"/uploaded-files/{f}/normalization", headers=h)
        for step in ("matching", "comparison", "discrepancies"):
            assert c.post(f"/analyses/{aid}/{step}", headers=h).status_code == 201
        c.post(f"/analyses/{aid}/mark-completed", headers=h)

        s = c.get(f"/analyses/{aid}/summary", headers=h).json()
        assert Decimal(s["total_recoverable_amount"]) == Decimal("173.00")
        assert (s["groups"]["needs_confirmation"]["count"], Decimal(s["groups"]["needs_confirmation"]["amount"])) == (1, Decimal("80.00"))
        assert s["excluded_refund_void_count"] == 1 and s["orders_not_checked_no_contract"] == 0

        cancelled = next(r for r in c.get(f"/analyses/{aid}/discrepancies", headers=h).json() if r["category"] == "CANCELLED_AFTER_PREPARATION")
        assert c.post(f"/discrepancies/{cancelled['id']}/cancellation", json={"cancelled_by": "PLATFORM"}, headers=h).status_code == 200
        assert Decimal(c.get(f"/analyses/{aid}/summary", headers=h).json()["total_recoverable_amount"]) == Decimal("237.00")
