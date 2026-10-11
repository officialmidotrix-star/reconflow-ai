"""
The amount-and-date fallback was rewritten for speed (an index instead of
scanning every remaining platform row for every POS order). The rule must not
have changed. This runs the ORIGINAL algorithm (copied verbatim below) and the
new one on hundreds of random inputs - repeated amounts, mixed currencies,
orders around local midnight, several day windows - and demands identical
matches, identical confidences and identical leftovers.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from modules.matching.models import ReconciliationMatch
from modules.matching.service import MatchingService


def _reference_fallback(svc, unmatched_pos, unmatched_platform, matches, analysis_id, tzinfo):
    """The pre-optimization implementation, unchanged."""
    for pos_txn in sorted(unmatched_pos.values(), key=lambda t: (t.occurred_at, t.id)):
        pos_local_date = pos_txn.occurred_at.astimezone(tzinfo).date()
        candidates = []
        for platform_txn in unmatched_platform.values():
            if platform_txn.currency_code != pos_txn.currency_code:
                continue
            if platform_txn.amount != pos_txn.amount:
                continue
            platform_local_date = platform_txn.occurred_at.astimezone(tzinfo).date()
            day_diff = abs((platform_local_date - pos_local_date).days)
            if day_diff <= svc._day_window:
                candidates.append((day_diff, platform_txn))
        if not candidates:
            continue
        day_diff, best = min(candidates, key=lambda pair: (pair[0], pair[1].id))
        confidence = max(svc._floor_confidence, svc._base_fallback_confidence - svc._decay_per_day * day_diff)
        matches.append(ReconciliationMatch(analysis_id=analysis_id, pos_transaction_id=pos_txn.id,
                                           platform_transaction_id=best.id, confidence_score=confidence))
        del unmatched_pos[pos_txn.id]
        del unmatched_platform[best.id]


def _world(rnd: random.Random):
    base = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)

    def txn(src):
        return SimpleNamespace(
            id=uuid.UUID(int=rnd.getrandbits(128)).hex,  # ids unrelated to time order
            occurred_at=base + timedelta(minutes=rnd.randint(0, 20 * 24 * 60)),  # includes late-evening UTC = next local day
            amount=rnd.choice([Decimal("10.00"), Decimal("10.50"), Decimal("20.00"), Decimal("20.0")]),
            currency_code=rnd.choice(["SAR", "SAR", "SAR", "AED"]),
            src=src,
        )

    pos = {t.id: t for t in (txn("pos") for _ in range(rnd.randint(1, 120)))}
    plat = {t.id: t for t in (txn("plat") for _ in range(rnd.randint(1, 150)))}
    return pos, plat


def _run(fn, svc, pos, plat, tz):
    p, q, m = dict(pos), dict(plat), []
    fn(svc, p, q, m, "a1", tz) if fn is _reference_fallback else fn(p, q, m, "a1", tz)
    return (
        [(x.pos_transaction_id, x.platform_transaction_id, x.confidence_score) for x in m],
        sorted(p), sorted(q),
    )


@pytest.mark.parametrize("window", [0, 1, 3, 7])
@pytest.mark.parametrize("tzname", ["Asia/Riyadh", "UTC", "America/Los_Angeles"])
def test_new_fallback_is_identical_to_the_original_on_random_inputs(window, tzname):
    svc = MatchingService(db=None, audit_logger=None, tz_lookup=None, day_window=window)
    tz = ZoneInfo(tzname)
    matched_somewhere = 0
    for seed in range(60):
        rnd = random.Random(f"{window}-{tzname}-{seed}")
        pos, plat = _world(rnd)
        old = _run(_reference_fallback, svc, pos, plat, tz)
        new = _run(svc._match_amount_and_date_fallback, svc, pos, plat, tz)
        assert new == old, f"seed {seed}: the new fallback disagrees with the original"
        matched_somewhere += len(old[0])
    assert matched_somewhere > 100  # the test really exercises matching, not just empty cases
