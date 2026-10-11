# Sample files — TEST DATA, not a real client

Use with the **TEST Branch 1 - Talabat (test rate 20%)** branch that appears when `RECONFLOW_SEED_DEMO_DATA=1`.
The 20% is a **test value**, not Talabat's real rate and not any client's contract.

Upload `TEST_pos_export.csv` as the POS export and `TEST_talabat_settlement.csv` as the platform settlement report.

## What you should see (worked out by hand; a test checks it)
| Order | What happened | Result |
|---|---|---|
| 1 | paid correctly | nothing |
| 2 | never paid, 200 | **recoverable 160.00** (200 less 20% commission) |
| 3 | cancelled by customer after preparation, 80 | **needs confirmation** (80.00, not in the headline yet) |
| 4 | refunded, no payment | not a problem, counted as "excluded" |
| 5 | paid 90 instead of 100 | **recoverable 8.00** (10 less 20%); commission 18 vs 20 is in your favour → to verify |
| 6 | paid 103 instead of 100 | **to verify** (an overpayment is not a loss) |
| 7 | commission charged 25 instead of 20 | **recoverable 5.00** |
| 99 | paid 55, no such order in the POS | **to verify** (unexpected money) |

**Headline: 173.00.** Then say who cancelled order 3 (`PLATFORM` or `CUSTOMER`) and the headline becomes **237.00** (80 less 20% = 64.00).
