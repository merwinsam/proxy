"""
Waterfall Engine (v3 — contract-driven).

Fixes vs. v2:
  - Tier membership now comes from tier_participants (the explicit
    contract), not from scanning every stakeholder by global
    entitlement_type/pool. A stakeholder assigned to Tier 2 but not Tier 3
    is only ever paid by Tier 2. This is what makes "Tier 2 recoups Alice
    and Bob; Tier 3 recoups Charlie" possible.
  - profit_split's creative share is now ALWAYS exactly
    100% - investor_pool_pct (paid - investor_share), and the UI only lets
    a producer set investor_pool_pct — there is no longer an independent
    creative_pool_pct input that can silently disagree with it.
  - gross_percentage tiers now REJECT (raise GrossPercentageOverflow) a
    configuration whose rates sum over 100%, instead of silently capping
    at 100% and reallocating. The primary check lives in
    engine/tier_participants.py at assignment time; this is a defensive
    second check in case tier_participants is ever edited outside the app.
  - profit_split's paid_this_run_cents now reflects what actually reached
    stakeholders, not what entered the tier (v4). If one pool has no
    participants assigned, its share is pushed back to remaining_cash and
    reported as residual — it is never counted as "paid". The amount that
    entered the tier is still visible via contractual_target_cents.

This module is pure — it takes a snapshot of current tier_progress
balances and tier_participants membership, and returns tier results plus
the mutated balances. engine/revenue.py persists everything atomically.
"""

from dataclasses import dataclass, field
from money import allocate_pro_rata
from engine.tier_participants import GrossPercentageOverflow


@dataclass
class TierResult:
    tier_id: int
    tier_number: int
    name: str
    tier_type: str
    contractual_target_cents: int
    paid_to_date_before_cents: int
    paid_this_run_cents: int
    remaining_after_cents: int
    per_stakeholder: dict = field(default_factory=dict)


@dataclass
class WaterfallResult:
    revenue_cents: int
    approved_expenses_cents: int
    tier_results: list
    residual_cents: int
    per_stakeholder_total: dict
    tier_level_progress: dict
    stakeholder_level_progress: dict


def calculate(revenue_cents, approved_expenses_cents, tiers, participants_by_tier,
              tier_level_progress, stakeholder_level_progress):
    remaining_cash = revenue_cents
    tier_results = []
    per_stakeholder_total = {}

    tier_level = {k: dict(v) for k, v in tier_level_progress.items()}
    stakeholder_level = {k: dict(v) for k, v in stakeholder_level_progress.items()}

    def credit(sid, amt):
        per_stakeholder_total[sid] = per_stakeholder_total.get(sid, 0) + amt

    for tier in tiers:
        per_stakeholder = {}
        tid = tier["id"]
        parts = participants_by_tier.get(tid, [])

        if tier["tier_type"] == "expense_cap":
            prog = tier_level.get(tid)
            target = prog["target_cents"] if prog else tier["cap_amount_cents"]
            paid_before = prog["paid_to_date_cents"] if prog else 0
            remaining_target = max(0, (target or 0) - paid_before)
            approved_outstanding = max(0, approved_expenses_cents - paid_before)
            paid = min(remaining_cash, remaining_target, approved_outstanding)
            remaining_cash -= paid
            if prog:
                prog["paid_to_date_cents"] = paid_before + paid
                tier_level[tid] = prog
            contractual_target = target or 0
            remaining_after = max(0, contractual_target - (paid_before + paid))

        elif tier["tier_type"] == "recoupment":
            keys = [(tid, p["stakeholder_id"]) for p in parts
                    if p["rule_type"] in ("recoupment_fixed", "recoupment_premium")
                    and (tid, p["stakeholder_id"]) in stakeholder_level]
            remaining_weights = {}
            for k in keys:
                prog = stakeholder_level[k]
                rem = max(0, (prog["target_cents"] or 0) - prog["paid_to_date_cents"])
                if rem > 0:
                    remaining_weights[k] = rem

            total_remaining = sum(remaining_weights.values())
            paid = min(remaining_cash, total_remaining)
            remaining_cash -= paid

            if paid > 0 and remaining_weights:
                for k, amt in allocate_pro_rata(paid, remaining_weights).items():
                    sid = k[1]
                    per_stakeholder[sid] = per_stakeholder.get(sid, 0) + amt
                    credit(sid, amt)
                    stakeholder_level[k]["paid_to_date_cents"] += amt

            contractual_target = sum((stakeholder_level[k]["target_cents"] or 0) for k in keys)
            paid_before = sum(stakeholder_level[k]["paid_to_date_cents"] for k in keys) - paid
            remaining_after = max(0, contractual_target - (paid_before + paid))

        elif tier["tier_type"] == "gross_percentage":
            gross_parts = [p for p in parts if p["rule_type"] == "gross_percentage"]
            total_pct = sum(p["rate_pct"] or 0 for p in gross_parts)
            if total_pct > 100:
                names = [(str(p["stakeholder_id"]), p["rate_pct"] or 0) for p in gross_parts]
                raise GrossPercentageOverflow(tier["name"], names, total_pct)

            weights = {p["stakeholder_id"]: p["rate_pct"] for p in gross_parts if p["rate_pct"] and p["rate_pct"] > 0}
            gross_amt = round(revenue_cents * total_pct / 100.0)
            paid = min(remaining_cash, gross_amt)
            remaining_cash -= paid
            if paid > 0 and weights:
                for sid, amt in allocate_pro_rata(paid, weights).items():
                    per_stakeholder[sid] = per_stakeholder.get(sid, 0) + amt
                    credit(sid, amt)
            contractual_target = gross_amt
            paid_before = 0
            remaining_after = 0  # resets every event — not cumulative

        elif tier["tier_type"] == "profit_split":
            entered_cents = remaining_cash
            remaining_cash = 0

            profit_parts = [p for p in parts if p["rule_type"] == "profit_points"]
            investor_parts = [p for p in profit_parts if p["pool"] == "investor"]
            creative_parts = [p for p in profit_parts if p["pool"] == "creative"]

            # Creative share is ALWAYS the remainder of investor_pool_pct — the UI
            # exposes only one control so these can never silently disagree.
            investor_share = round(entered_cents * tier["investor_pool_pct"] / 100.0)
            creative_share = entered_cents - investor_share

            inv_weights = {p["stakeholder_id"]: p["points"] for p in investor_parts if p["points"] and p["points"] > 0}
            cre_weights = {p["stakeholder_id"]: p["points"] for p in creative_parts if p["points"] and p["points"] > 0}

            if inv_weights:
                for sid, amt in allocate_pro_rata(investor_share, inv_weights).items():
                    per_stakeholder[sid] = per_stakeholder.get(sid, 0) + amt
                    credit(sid, amt)
            else:
                remaining_cash += investor_share  # nobody assigned — falls through as residual, NOT "paid"

            if cre_weights:
                for sid, amt in allocate_pro_rata(creative_share, cre_weights).items():
                    per_stakeholder[sid] = per_stakeholder.get(sid, 0) + amt
                    credit(sid, amt)
            else:
                remaining_cash += creative_share  # ditto

            # paid_this_run_cents is what actually reached stakeholders, not
            # what entered the tier — if one pool has nobody assigned, its
            # share is pushed back to remaining_cash above and must NOT be
            # reported as "paid". contractual_target_cents instead carries
            # the full amount that entered the tier, so a Waterfall Builder
            # or ledger reader can see both "entered $100,000" and
            # "distributed $50,000" without either overstating the other.
            paid = sum(per_stakeholder.values())
            contractual_target = entered_cents
            paid_before = 0
            remaining_after = 0

        else:
            paid = 0
            contractual_target = 0
            paid_before = 0
            remaining_after = 0

        tier_results.append(
            TierResult(
                tier_id=tid,
                tier_number=tier["tier_number"],
                name=tier["name"],
                tier_type=tier["tier_type"],
                contractual_target_cents=contractual_target,
                paid_to_date_before_cents=paid_before,
                paid_this_run_cents=paid,
                remaining_after_cents=remaining_after,
                per_stakeholder=per_stakeholder,
            )
        )

        if remaining_cash <= 0:
            break

    return WaterfallResult(
        revenue_cents=revenue_cents,
        approved_expenses_cents=approved_expenses_cents,
        tier_results=tier_results,
        residual_cents=remaining_cash,
        per_stakeholder_total=per_stakeholder_total,
        tier_level_progress=tier_level,
        stakeholder_level_progress=stakeholder_level,
    )
