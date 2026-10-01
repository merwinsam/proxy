# Changes in v3

Four issues from a second review, focused on contract fidelity.

## 1. Profit-split percentage bug

**Before:** the Waterfall Builder let a producer enter `investor_pool_pct`
and `creative_pool_pct` independently, but the engine only ever used
`investor_pool_pct` and gave the creative pool whatever remained — so
entering 60% / 30% silently produced 60% / 40%. The `creative_pool_pct`
field looked like it controlled something; it didn't.

**Fix:** the tier-creation form now exposes a single **Investor pool %**
slider; the creative share is always displayed and stored as exactly
`100 - investor_pool_pct`, and the engine computes it the same way
(`creative_share = paid - investor_share`), so the two values can never
disagree. Verified: a tier stored with a stale/inconsistent
`creative_pool_pct` still produces the mathematically correct remainder.

## 2. Tiers weren't connected to specific rightsholders

**Before:** every recoupment tier picked up *every* investor with a
recoupment entitlement, and every gross-percentage tier picked up *every*
percentage-type stakeholder — driven by each stakeholder's global
`entitlement_type`/`pool`. There was no way to model "Tier 2 recoups Alice
and Bob; Tier 3 recoups Charlie."

**Fix:** a new `tier_participants` table is now the actual contract: which
stakeholder belongs to which tier, under which rule (`recoupment_fixed`,
`recoupment_premium`, `gross_percentage`, `profit_points`), at what rate,
and in which pool. The Waterfall Builder page has an explicit "assign
participant to this tier" control per tier. The engine
(`engine/waterfall.py`) and balance tracker (`engine/tier_progress.py`) now
read membership from `tier_participants`, never from a stakeholder's
global defaults. Verified with the review's own example: a Sales Agent
tier, an "Alice + Bob" recoupment tier, and a "Charlie only" recoupment
tier — Charlie never appears in Alice/Bob's tier and vice versa.

A stakeholder's `entitlement_type`/`pool`/`investment_amount`/`premium_pct`
on the Rights Registry are now explicitly **default contract terms** —
used to prefill a new tier assignment — not something the engine reads
directly (except `investment_amount_cents`, which remains each
stakeholder's one canonical principal across tiers).

## 3. Rights history wasn't enough to reproduce a historical calculation

**Before:** only `points` changes were versioned; a change to investment
amount, premium, entitlement type, or pool silently overwrote history.

**Fix:** `stakeholder_amendments` (points-only) is replaced by
`agreement_versions` — a full contract-term snapshot (points,
entitlement_type, investment, premium, pool) written every time *any* of
those fields change. Every `distributions` row now carries the
`agreement_version_id` that was current at calculation time, so a past
distribution can point at exactly which version of the agreement produced
it. The Audit Ledger's payout view has a **"Why did X receive $Y?"**
expander per payout that walks revenue event → tier → agreement version →
amount, reconstructed entirely from these records.

## 4. Gross percentages could silently overflow 100%

**Before:** nothing stopped a producer from assigning gross-percentage
stakeholders whose rates summed past 100%.

**Fix:** `engine/tier_participants.py::add_participant()` computes the
tier's total gross percentage including the new assignment and raises
`GrossPercentageOverflow` — with a full breakdown of every participant and
their rate — if it exceeds 100%, rather than silently capping and
reallocating. The Waterfall Builder shows this as a red error box; the
Revenue page refuses to process revenue against an invalid contract at
all, rather than quietly "fixing" it. `engine/waterfall.py` carries the
same check as a defensive second line in case `tier_participants` is ever
edited outside the app. Verified against the review's own example: Alice
70% + Bob 30% is accepted; adding Carol at 20% (120% total) is rejected
with the exact breakdown shown in the review's mockup.

## Not done in this pass

Per the reviewer's own note, the fixed-cap-vs-dynamic-recoupable expense
tier distinction is explicitly deferred ("I wouldn't complicate V2 with
this yet"). The full `CONTRACT → AGREEMENT VERSION → RIGHTSHOLDER → TIER
PARTICIPATION → REVENUE EVENT → CALCULATION → DISTRIBUTION → PAYOUT →
AUDIT EXPLANATION` milestone is now substantially built (tier_participants
+ agreement_versions + the per-distribution "why" expander), but a
dedicated full-page audit-explanation screen — the "WHY DID ANNA RECEIVE
$5,100?" waterfall-diagram view — is still a natural next step, not
implemented here.
