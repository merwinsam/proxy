"""
Tier progress: the persistent "contractual target minus paid-to-date"
balance that makes the waterfall stateful across multiple revenue events.

v3: balances are now derived from tier_participants (the explicit
contract) rather than scanning all stakeholders by global entitlement_type
and pool — only a stakeholder actually assigned to a tier gets a balance
for it.

v4: a contract amendment no longer leaves a stale target sitting in
tier_progress. rebase_stakeholder_recoupment_targets() and
rebase_participant_target() recompute the target and apply the difference
as a logged adjustment (tier_progress_adjustments) — paid_to_date is
preserved, and the change is auditable.
"""

from database.db import get_conn, today
from engine.tier_participants import list_participants_for_project


def ensure_progress_rows(conn, project_id, tiers):
    """Create any missing tier_progress rows from the current contract. Idempotent."""
    participants_by_tier = list_participants_for_project(conn, project_id)

    for tier in tiers:
        if tier["tier_type"] == "expense_cap":
            existing = conn.execute(
                "SELECT id FROM tier_progress WHERE tier_id=? AND stakeholder_id IS NULL",
                (tier["id"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO tier_progress
                       (project_id, tier_id, stakeholder_id, contractual_target_cents, paid_to_date_cents)
                       VALUES (?, ?, NULL, ?, 0)""",
                    (project_id, tier["id"], tier["cap_amount_cents"]),
                )

        elif tier["tier_type"] == "recoupment":
            for p in participants_by_tier.get(tier["id"], []):
                if p["rule_type"] not in ("recoupment_fixed", "recoupment_premium"):
                    continue
                existing = conn.execute(
                    "SELECT id FROM tier_progress WHERE tier_id=? AND stakeholder_id=?",
                    (tier["id"], p["stakeholder_id"]),
                ).fetchone()
                if existing:
                    continue
                principal = conn.execute(
                    "SELECT investment_amount_cents FROM stakeholders WHERE id=?", (p["stakeholder_id"],)
                ).fetchone()["investment_amount_cents"]
                if p["rule_type"] == "recoupment_fixed":
                    target = principal
                else:  # recoupment_premium — this participation's own rate, not the stakeholder's global default
                    target = round(principal * (1 + (p["rate_pct"] or 0) / 100.0))
                if p["cap_cents"]:
                    target = min(target, p["cap_cents"])
                conn.execute(
                    """INSERT INTO tier_progress
                       (project_id, tier_id, stakeholder_id, contractual_target_cents, paid_to_date_cents)
                       VALUES (?, ?, ?, ?, 0)""",
                    (project_id, tier["id"], p["stakeholder_id"], target),
                )
        # gross_percentage and profit_split are uncapped — no progress rows.


def load_snapshot(conn, project_id):
    tier_level = {}
    stakeholder_level = {}
    rows = conn.execute("SELECT * FROM tier_progress WHERE project_id=?", (project_id,)).fetchall()
    for r in rows:
        entry = {
            "row_id": r["id"],
            "target_cents": r["contractual_target_cents"],
            "paid_to_date_cents": r["paid_to_date_cents"],
        }
        if r["stakeholder_id"] is None:
            tier_level[r["tier_id"]] = entry
        else:
            stakeholder_level[(r["tier_id"], r["stakeholder_id"])] = entry
    return tier_level, stakeholder_level


def persist_updates(conn, tier_level, stakeholder_level):
    for entry in tier_level.values():
        conn.execute(
            "UPDATE tier_progress SET paid_to_date_cents=? WHERE id=?",
            (entry["paid_to_date_cents"], entry["row_id"]),
        )
    for entry in stakeholder_level.values():
        conn.execute(
            "UPDATE tier_progress SET paid_to_date_cents=? WHERE id=?",
            (entry["paid_to_date_cents"], entry["row_id"]),
        )


def load_progress_for_preview(project_id, tiers):
    """Read-only snapshot for the Waterfall Builder's what-if preview (no writes)."""
    with get_conn() as conn:
        ensure_progress_rows(conn, project_id, tiers)
        return load_snapshot(conn, project_id)


def _compute_target(investment_cents, rule_type, rate_pct, cap_cents):
    if rule_type == "recoupment_fixed":
        target = investment_cents
    elif rule_type == "recoupment_premium":
        target = round(investment_cents * (1 + (rate_pct or 0) / 100.0))
    else:
        return None
    if cap_cents:
        target = min(target, cap_cents)
    return target


def _rebase_target_in_conn(conn, tier_progress_id, new_target_cents, effective_date, note):
    """Adjust a tier_progress row's target by a logged delta — never a silent
    overwrite. paid_to_date is untouched, so remaining = new target -
    paid_to_date falls out correctly, and the change stays auditable."""
    row = conn.execute("SELECT * FROM tier_progress WHERE id=?", (tier_progress_id,)).fetchone()
    old_target = row["contractual_target_cents"] or 0
    delta = new_target_cents - old_target
    if delta == 0:
        return

    conn.execute(
        """INSERT INTO tier_progress_adjustments
           (tier_progress_id, effective_date, old_target_cents, new_target_cents, delta_cents, note, created_on)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tier_progress_id, effective_date, old_target, new_target_cents, delta, note, today()),
    )
    conn.execute(
        "UPDATE tier_progress SET contractual_target_cents=? WHERE id=?",
        (new_target_cents, tier_progress_id),
    )
    sign = "+" if delta > 0 else ""
    conn.execute(
        """INSERT INTO ledger (project_id, revenue_event_id, entry_date, entry_type, description, amount_cents, related_id)
           VALUES (?, NULL, ?, 'target_adjustment', ?, 0, ?)""",
        (
            row["project_id"], today(),
            f"Recoupment target adjusted {sign}${delta/100:,.2f} "
            f"(was ${old_target/100:,.2f}, now ${new_target_cents/100:,.2f}) — {note}",
            tier_progress_id,
        ),
    )


def rebase_stakeholder_recoupment_targets(stakeholder_id, note, effective_date=None):
    """
    Called after a stakeholder's investment_amount_cents changes (Rights
    Registry amendment). Recomputes the target for every recoupment
    tier_progress row this stakeholder participates in, using the CURRENT
    investment amount and each tier's own participation rate, and applies
    the difference as a logged adjustment rather than silently overwriting
    the old target.
    """
    with get_conn() as conn:
        investment = conn.execute(
            "SELECT investment_amount_cents FROM stakeholders WHERE id=?", (stakeholder_id,)
        ).fetchone()["investment_amount_cents"]
        rows = conn.execute(
            """SELECT tp.id AS tier_progress_id, tpar.rule_type, tpar.rate_pct, tpar.cap_cents
               FROM tier_progress tp
               JOIN tier_participants tpar ON tpar.tier_id = tp.tier_id AND tpar.stakeholder_id = tp.stakeholder_id
               WHERE tp.stakeholder_id=?""",
            (stakeholder_id,),
        ).fetchall()
        for r in rows:
            new_target = _compute_target(investment, r["rule_type"], r["rate_pct"], r["cap_cents"])
            if new_target is not None:
                _rebase_target_in_conn(conn, r["tier_progress_id"], new_target, effective_date or today(), note)


def rebase_participant_target(tier_id, stakeholder_id, note, effective_date=None):
    """Called after a tier_participant's own rate/cap changes (editing the
    tier assignment directly, not the Rights Registry)."""
    with get_conn() as conn:
        tp_row = conn.execute(
            "SELECT * FROM tier_progress WHERE tier_id=? AND stakeholder_id=?", (tier_id, stakeholder_id)
        ).fetchone()
        if not tp_row:
            return
        participant = conn.execute(
            "SELECT * FROM tier_participants WHERE tier_id=? AND stakeholder_id=?", (tier_id, stakeholder_id)
        ).fetchone()
        investment = conn.execute(
            "SELECT investment_amount_cents FROM stakeholders WHERE id=?", (stakeholder_id,)
        ).fetchone()["investment_amount_cents"]
        new_target = _compute_target(investment, participant["rule_type"], participant["rate_pct"], participant["cap_cents"])
        if new_target is not None:
            _rebase_target_in_conn(conn, tp_row["id"], new_target, effective_date or today(), note)


def list_adjustments(tier_progress_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tier_progress_adjustments WHERE tier_progress_id=? ORDER BY id",
            (tier_progress_id,),
        ).fetchall()
        return [dict(r) for r in rows]
