# How the numbers work now (Batch 2) — in simple words

**The headline = "what you can claim back".** It counts only money the restaurant was *underpaid*, and only the part the platform would really have paid: **after its commission**. Example: an order of 200 that was never paid, commission 15% → the claim is 170, not 200.

Every problem found is put in exactly one box:

| Box | What it means | In the headline? |
|---|---|---|
| **Recoverable** | Underpaid. Missing payment, a payment lower than the order, or commission charged above the contract. Shown net of commission. | **Yes** |
| **Needs confirmation** | Order cancelled after preparation. Someone must say who cancelled it. | No, until confirmed |
| **Needs contract** | Looks like a loss, but there is no (or more than one) commission rate for that day, so the real amount cannot be worked out. Shown gross, clearly marked. | No |
| **To verify** | Not a loss: money received for an order we cannot find, a payment *higher* than the order, commission charged *below* the contract. | **No** |
| **Not claimable** | You confirmed the restaurant itself cancelled it. | No |
| **Needs re-run** | Calculated before these rules existed. Run the analysis again. | No |

**Cancelled orders.** Say who cancelled with `POST /discrepancies/{id}/cancellation` and `{"cancelled_by": "PLATFORM"}` (or `CUSTOMER` or `RESTAURANT`). Platform or customer → claimable (net). Restaurant → not claimable. The answer is remembered if you run the analysis again. Whether a platform pays for a customer-cancelled order is the platform's own rule; the letter will say who cancelled and let the platform decide.

**Refunded or void orders** with no payment are not problems (no payment is expected). They are counted in `excluded_refund_void_count` so nothing disappears quietly.

**Nothing is hidden any more.** `orders_not_checked_no_contract` says how many matched orders could not be checked for amount or commission errors because no single commission rate applied. Before, this was skipped silently.

**Status words.** The cancelled/refunded detection now reads whole words (English and Arabic), so `CANCELLED_BY_CUSTOMER` works and "Cancellation fee waived" does not trigger. The Arabic list is a starting set: real exports have never been checked.

**Still to come:** the PDF for the platform (Batch 5) still lists every row. **Do not send the current PDF to a platform yet.**
**Known limit until Batch 4:** a branch working with two platforms has no single commission rate, so it shows "needs contract" instead of a guess.
