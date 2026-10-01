"""
Tier Participants — the contract, explicit.

Instead of the engine inferring "every investor-pool recoupment-typed
stakeholder belongs to every recoupment tier," the contract now says
exactly who belongs to which tier and on what terms. This is what lets you
model something like:

    Tier 1 — Sales Agent           10% gross, Sales Agent Co. only
    Tier 2 — Investor Group A      Alice + Bob recoup
    Tier 3 — Investor Group B      Charlie recoups
    Tier 4 — Net profits           everyone, by points

A stakeholder simply not assigned to a tier is never touched by it.

Gross-percentage tiers reject an over-100% configuration outright rather
than silently capping and reallocating — a producer who fat-fingers 120%
across three participants gets told exactly that, not a quietly "fixed"
contract.
"""

from database.db import get_conn


class GrossPercentageOverflow(ValueError):
    def __init__(self, tier_name, participants, total_pct):
        self.tier_name = tier_name
        self.participants = participants  # list of (name, rate_pct)
        self.total_pct = total_pct
        breakdown = ", ".join(f"{n} {p:g}%" for n, p in participants)
        super().__init__(
            f"Gross participation on '{tier_name}' totals {total_pct:g}% ({breakdown}) — "
            f"cannot exceed 100%. Fix the rates before this contract can be processed."
        )


def _gross_pct_total(conn, tier_id, exclude_participant_id=None):
    rows = conn.execute(
        """SELECT tp.id, tp.rate_pct, s.name FROM tier_participants tp
           JOIN stakeholders s ON s.id = tp.stakeholder_id
           WHERE tp.tier_id=? AND tp.rule_type='gross_percentage'""",
        (tier_id,),
    ).fetchall()
    rows = [r for r in rows if r["id"] != exclude_participant_id]
    return [(r["name"], r["rate_pct"] or 0) for r in rows]


def add_participant_in_conn(conn, tier_id, stakeholder_id, rule_type, pool=None, rate_pct=None, points=None, cap_cents=None):
    """Same as add_participant, but runs against a connection/transaction the
    caller already owns (e.g. app.py's seed, or another multi-step write) —
    never opens its own connection, so it can't deadlock against an
    in-progress transaction."""
    if rule_type == "gross_percentage":
        tier = conn.execute("SELECT name FROM waterfall_tiers WHERE id=?", (tier_id,)).fetchone()
        existing = _gross_pct_total(conn, tier_id)
        candidate_name = conn.execute("SELECT name FROM stakeholders WHERE id=?", (stakeholder_id,)).fetchone()["name"]
        all_participants = existing + [(candidate_name, rate_pct or 0)]
        total = sum(p for _, p in all_participants)
        if total > 100:
            raise GrossPercentageOverflow(tier["name"], all_participants, total)

    conn.execute(
        """INSERT INTO tier_participants (tier_id, stakeholder_id, rule_type, pool, rate_pct, points, cap_cents)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tier_id, stakeholder_id) DO UPDATE SET
             rule_type=excluded.rule_type, pool=excluded.pool, rate_pct=excluded.rate_pct,
             points=excluded.points, cap_cents=excluded.cap_cents""",
        (tier_id, stakeholder_id, rule_type, pool, rate_pct, points, cap_cents),
    )


def add_participant(tier_id, stakeholder_id, rule_type, pool=None, rate_pct=None, points=None, cap_cents=None):
    with get_conn() as conn:
        add_participant_in_conn(conn, tier_id, stakeholder_id, rule_type, pool, rate_pct, points, cap_cents)


def update_participant(participant_id, rate_pct=None, points=None, pool=None, cap_cents=None, note="", effective_date=None):
    """
    Edit an EXISTING assignment's terms. If it's a recoupment rule and the
    rate or cap actually changes, the stakeholder's tier_progress target is
    rebased (adjusted by the delta, paid_to_date preserved) rather than
    left stale — see engine/tier_progress.py.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tier_participants WHERE id=?", (participant_id,)).fetchone()
        if not row:
            return
        new_rate = rate_pct if rate_pct is not None else row["rate_pct"]
        new_points = points if points is not None else row["points"]
        new_pool = pool if pool is not None else row["pool"]
        new_cap = cap_cents if cap_cents is not None else row["cap_cents"]
        rate_or_cap_changed = (new_rate != row["rate_pct"]) or (new_cap != row["cap_cents"])

        if row["rule_type"] == "gross_percentage" and new_rate != row["rate_pct"]:
            tier = conn.execute("SELECT name FROM waterfall_tiers WHERE id=?", (row["tier_id"],)).fetchone()
            existing = _gross_pct_total(conn, row["tier_id"], exclude_participant_id=participant_id)
            name = conn.execute("SELECT name FROM stakeholders WHERE id=?", (row["stakeholder_id"],)).fetchone()["name"]
            all_participants = existing + [(name, new_rate or 0)]
            total = sum(p for _, p in all_participants)
            if total > 100:
                raise GrossPercentageOverflow(tier["name"], all_participants, total)

        conn.execute(
            "UPDATE tier_participants SET rate_pct=?, points=?, pool=?, cap_cents=? WHERE id=?",
            (new_rate, new_points, new_pool, new_cap, participant_id),
        )
        tier_id, stakeholder_id, rule_type = row["tier_id"], row["stakeholder_id"], row["rule_type"]

    if rate_or_cap_changed and rule_type in ("recoupment_fixed", "recoupment_premium"):
        from engine import tier_progress as tp  # deferred to avoid circular import
        tp.rebase_participant_target(
            tier_id, stakeholder_id, note or "Tier participation terms amended", effective_date
        )


def remove_participant(participant_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM tier_participants WHERE id=?", (participant_id,))


def list_participants(tier_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT tp.*, s.name AS stakeholder_name, s.role AS stakeholder_role, s.creator_id
               FROM tier_participants tp JOIN stakeholders s ON s.id = tp.stakeholder_id
               WHERE tp.tier_id=? ORDER BY tp.id""",
            (tier_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_participants_for_project(conn, project_id):
    """Used inside an existing transaction (engine/revenue.py, tier_progress.py)."""
    rows = conn.execute(
        """SELECT tp.* FROM tier_participants tp
           JOIN waterfall_tiers wt ON wt.id = tp.tier_id
           WHERE wt.project_id=?""",
        (project_id,),
    ).fetchall()
    by_tier = {}
    for r in rows:
        by_tier.setdefault(r["tier_id"], []).append(dict(r))
    return by_tier
