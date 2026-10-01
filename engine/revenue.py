"""
Revenue processing — the single function that owns a revenue event.

v3: tier membership comes from tier_participants (see
engine/tier_progress.py and engine/waterfall.py), and every distribution
row is tagged with the stakeholder's agreement_version_id.

v4:
  - That agreement version is now resolved BY EFFECTIVE DATE
    (current_agreement_version_id's default as_of_date), not by "whichever
    row was entered last" — a future-dated amendment can't leak into
    today's distribution.
  - Each distribution also freezes a SNAPSHOT of the tier_participants
    terms that produced it (rule_type, pool, rate_pct, points, cap_cents).
    tier_participants itself stays mutable (a producer can edit it later),
    but the snapshot on the distribution row can't change after the fact —
    "why did this payout happen" stays answerable even if the contract is
    amended six months later.
  - process_held_cash() addresses money that a revenue event couldn't
    fully allocate (e.g. a tier hadn't been configured yet). That residual
    sits in distributable_cash_cents; this reprocesses it through the
    CURRENT contract as its own event, without double-counting it as new
    revenue.
"""

from database.db import get_conn, today
from engine import waterfall as wf_engine
from engine import tier_progress as tp
from engine.registry import current_agreement_version_id
from engine.tier_participants import list_participants_for_project, GrossPercentageOverflow


def _run_and_record(conn, project_id, amount_cents, source, territory, is_sweep):
    tiers = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM waterfall_tiers WHERE project_id=? ORDER BY tier_number", (project_id,)
        ).fetchall()
    ]

    tp.ensure_progress_rows(conn, project_id, tiers)
    tier_level, stakeholder_level = tp.load_snapshot(conn, project_id)
    participants_by_tier = list_participants_for_project(conn, project_id)

    approved_cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS t FROM expenses WHERE project_id=? AND status='ALLOCATED'",
        (project_id,),
    ).fetchone()["t"]

    # May raise GrossPercentageOverflow — deliberately NOT caught here. The
    # caller (a Streamlit page) decides how to surface it; we never want a
    # bad contract to be silently "fixed" and processed anyway.
    result = wf_engine.calculate(
        amount_cents, approved_cents, tiers, participants_by_tier, tier_level, stakeholder_level
    )

    cur = conn.execute(
        """INSERT INTO revenue_events (project_id, source, territory, amount_cents, is_sweep, entry_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (project_id, source, territory, amount_cents, 1 if is_sweep else 0, today()),
    )
    revenue_event_id = cur.lastrowid

    conn.execute(
        """INSERT INTO ledger (project_id, revenue_event_id, entry_date, entry_type, description, amount_cents, related_id)
           VALUES (?, ?, ?, ?, ?, ?, NULL)""",
        (
            project_id, revenue_event_id, today(),
            "held_cash_sweep" if is_sweep else "revenue",
            f"{'Held cash reprocessed' if is_sweep else 'Revenue received'}: {source}"
            + (f" ({territory})" if territory else ""),
            0 if is_sweep else amount_cents,  # a sweep isn't new inflow — it was already counted as revenue
        ),
    )

    stakeholders = {
        r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM stakeholders WHERE project_id=?", (project_id,)
        ).fetchall()
    }
    # Each stakeholder's agreement version AS OF TODAY — resolved by
    # effective_date, not insertion order (see engine/registry.py).
    version_ids = {sid: current_agreement_version_id(conn, sid, today()) for sid in stakeholders}

    for tier in result.tier_results:
        conn.execute(
            """INSERT INTO ledger (project_id, revenue_event_id, entry_date, entry_type, description, amount_cents, related_id)
               VALUES (?, ?, ?, 'tier_info', ?, 0, ?)""",
            (
                project_id, revenue_event_id, today(),
                f"Tier {tier.tier_number} — {tier.name}: "
                f"{'$%.2f' % (tier.paid_this_run_cents/100)} distributed this event "
                f"(remaining balance: {'$%.2f' % (tier.remaining_after_cents/100)})",
                tier.tier_id,
            ),
        )

        if tier.tier_type == "expense_cap" and tier.paid_this_run_cents > 0:
            conn.execute(
                """INSERT INTO ledger (project_id, revenue_event_id, entry_date, entry_type, description, amount_cents, related_id)
                   VALUES (?, ?, ?, 'expense_payment', ?, ?, ?)""",
                (
                    project_id, revenue_event_id, today(),
                    f"Expense payments (Tier {tier.tier_number})",
                    -tier.paid_this_run_cents, tier.tier_id,
                ),
            )

        tier_participants = participants_by_tier.get(tier.tier_id, [])
        participant_by_sid = {p["stakeholder_id"]: p for p in tier_participants}

        for sid, amt in tier.per_stakeholder.items():
            if amt <= 0:
                continue
            name = stakeholders.get(sid, {}).get("name", "Unknown")
            conn.execute(
                """INSERT INTO ledger (project_id, revenue_event_id, entry_date, entry_type, description, amount_cents, related_id)
                   VALUES (?, ?, ?, 'distribution', ?, ?, ?)""",
                (project_id, revenue_event_id, today(), f"Distribution: {name} ({tier.name})", -amt, sid),
            )
            p = participant_by_sid.get(sid, {})
            conn.execute(
                """INSERT INTO distributions
                   (project_id, revenue_event_id, stakeholder_id, tier_id, agreement_version_id,
                    rule_type, pool, rate_pct, points, cap_cents, amount_cents, entry_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, revenue_event_id, sid, tier.tier_id, version_ids.get(sid),
                    p.get("rule_type"), p.get("pool"), p.get("rate_pct"), p.get("points"), p.get("cap_cents"),
                    amt, today(),
                ),
            )

    tp.persist_updates(conn, result.tier_level_progress, result.stakeholder_level_progress)

    if is_sweep:
        # We're consuming the held balance itself, not adding new revenue on
        # top of it — replace, don't accumulate, and never touch
        # revenue_received_cents (this cash was already counted once).
        conn.execute(
            "UPDATE projects SET distributable_cash_cents = ? WHERE id = ?",
            (result.residual_cents, project_id),
        )
    else:
        conn.execute(
            """UPDATE projects
               SET revenue_received_cents = revenue_received_cents + ?,
                   distributable_cash_cents = distributable_cash_cents + ?
               WHERE id = ?""",
            (amount_cents, result.residual_cents, project_id),
        )

    for sid, total in result.per_stakeholder_total.items():
        if total <= 0:
            continue
        kyc = stakeholders.get(sid, {}).get("kyc_status", "KYC_REQUIRED")
        conn.execute(
            """INSERT INTO payouts (stakeholder_id, revenue_event_id, amount_cents, status, entry_date)
               VALUES (?, ?, ?, ?, ?)""",
            (sid, revenue_event_id, total, kyc, today()),
        )

    return result, revenue_event_id


def process_revenue_event(project_id, source, territory, amount_cents):
    """Process NEW incoming revenue. Counts toward revenue_received_cents."""
    with get_conn() as conn:
        return _run_and_record(conn, project_id, amount_cents, source, territory, is_sweep=False)


def process_held_cash(project_id):
    """
    Reprocess whatever is currently sitting in distributable_cash_cents
    (e.g. a prior event's residual, left over because a tier wasn't
    configured yet) through the CURRENT contract, as its own revenue event.
    This is NOT new revenue — it's cash the project already received and
    holds — so it does not touch revenue_received_cents. Returns None if
    there's nothing to sweep.
    """
    with get_conn() as conn:
        held = conn.execute(
            "SELECT distributable_cash_cents FROM projects WHERE id=?", (project_id,)
        ).fetchone()["distributable_cash_cents"]
        if not held or held <= 0:
            return None, None
        return _run_and_record(conn, project_id, held, "Held Cash Sweep", None, is_sweep=True)
