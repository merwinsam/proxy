# Changes in v2

Six correctness issues from a review of the first prototype, and how each was fixed.

## 1. The waterfall forgot history (the critical bug)

**Before:** every revenue event recomputed a recoupment or expense-cap
target from scratch (`investment * (1 + premium%)`) and paid up to that
full target again, regardless of what had already been paid. Two $150,000
events could pay an $80,000-investor $192,000 against a $96,000 entitlement.

**Fix:** `engine/tier_progress.py` gives every capped entitlement (each
recoupment stakeholder, each expense-cap tier) a persistent
`contractual_target_cents` / `paid_to_date_cents` row. `engine/waterfall.py`
now computes `remaining = target - paid_to_date` and never pays more than
that remainder; `engine/revenue.py` persists the updated `paid_to_date`
inside the same transaction as everything else. Verified: processing
$150,000 twice against the demo project pays Sarah Chen exactly $60,000
total (her contractual target) across both events, not $120,000.

## 2. Duplicate revenue entries + double-counted tier totals

**Before:** `record_waterfall_result()` wrote one `"Revenue received"`
ledger entry for the full amount, and `5_Revenue.py` separately wrote a
second `"Netflix License"` entry for the same amount — one payment, two
ledger lines. Separately, a `tier_completed` entry for `-$96,000` was
written *alongside* the individual `-$60,000` / `-$36,000` stakeholder
distributions that summed to the same $96,000 — counting the same money
twice.

**Fix:** `engine/revenue.py::process_revenue_event()` is now the single
function that owns a revenue event, in one SQLite transaction. It writes
exactly one `revenue` entry. Tier markers are now `tier_info` entries with
`amount_cents = 0` (informational only — "Tier 2: $96,000 of $96,000
satisfied"). Real money entries are only the actual transfers: one
aggregate `expense_payment` line per expense-cap tier, and one
`distribution` line per stakeholder. The ledger now reconciles to exactly
$0 (verified in testing).

## 3. Payout batching summed all-time distributions

**Before:** `sync_payouts_from_distributions()` summed *every* historical
distribution for a stakeholder into one payout row. Once that row was
marked PAID and a *new* distribution came in, the next sync created a new
payout row from `SUM(all distributions)` — which included the already-paid
amount. A stakeholder with $10,000 paid and a further $5,000 distributed
could be shown a $15,000 second payout instead of $5,000.

**Fix:** `payouts` now has a `UNIQUE(stakeholder_id, revenue_event_id)`
constraint — one batch per stakeholder per revenue event, created directly
by `process_revenue_event()`. Paying one batch can't inflate another.
Verified: paying Sarah Chen's event-#1 batch leaves her event-#2 batch
amount untouched.

## 4. Recoupment split by original principal, not remaining entitlement

**Before:** partial recoupment payments were split pro-rata by each
investor's original investment amount. Two investors with equal principal
but different premiums (say 20% vs. 50%) would be paid 50/50 even though
their contractual targets ($60,000 vs. $75,000) differ.

**Fix:** the recoupment tier now splits by each stakeholder's *remaining*
entitlement (`target - paid_to_date`), using largest-remainder pro-rata
allocation (`money.allocate_pro_rata`) so shares sum exactly to the cents.
Verified with the review's own example: $50,000 against a $60k/$75k target
splits $22,222.22 / $27,777.78 — proportional to target, not principal.

## 5. Entitlement types were cosmetic

**Before:** the Rights Registry let you pick `percentage`,
`fixed_recoupment`, `recoupment_plus_premium`, or `continuing_participation`,
but the engine only ever looked at `pool`, `investment_amount`,
`premium_pct` and `points` — the entitlement type itself didn't change the
math.

**Fix:** the engine now branches on `entitlement_type`:
- `fixed_recoupment` → recoupment tier, target = investment (no premium)
- `recoupment_plus_premium` → recoupment tier, target = investment × (1 + premium%)
- `percentage` → a new `gross_percentage` tier type: an off-the-top cut of
  *gross* revenue each event, sized by `points` read as a percent (0–100)
- `continuing_participation` → profit-split tier only, by points

`fixed_recoupment` / `recoupment_plus_premium` stakeholders can *also* earn
points in the profit-split tier (matching the demo, where investors hold
both a recoupment entitlement and profit points); pure `percentage`
stakeholders do not participate in the profit split, since they're already
taking a cut off the top.

## 6. Points were silently overwritten

**Before:** there was no way to change a stakeholder's points without
losing the previous value.

**Fix:** `engine/registry.py::amend_points()` writes a
`stakeholder_amendments` row (before → after, effective date, note) and
only then updates the current value. The Rights Registry page shows this
history per stakeholder and lets you post new amendments.

## Also: money moved to integer cents

Every amount column in the database is now `INTEGER` cents rather than
`REAL` dollars (`money.py` handles the dollar ↔ cents conversion at the UI
boundary). This removes float-rounding drift from recoupment math and
pro-rata splits, and the largest-remainder allocation in
`money.allocate_pro_rata` guarantees split amounts always sum exactly to
the total being divided — no missing or extra cent.

## Not done in this pass

Per the review's own prioritization, **source-material / contribution
tracking** (the "why does Anna hold 3% of net proceeds" agreement layer)
and **Stripe Connect** integration are follow-on work, not part of this
fix. The append-only amendment history (#6 above) is a first step toward
the former.
