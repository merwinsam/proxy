"""
Provenance — "why did X receive $Y from revenue event #N?"

Reconstructs the chain: revenue event -> tier -> agreement version in
effect -> amount, from the records already written by engine/revenue.py.
This is a read-only convenience for the Audit Ledger page; it doesn't
change how anything is calculated.

v4: the "on what terms" part of the answer now comes from the SNAPSHOT
columns on the distribution row itself (rule_type, pool, rate_pct, points,
cap_cents) — frozen at calculation time — rather than a live join against
tier_participants. tier_participants is mutable (a producer can edit an
assignment's rate later); the snapshot is not, so this explanation can't
silently change after the fact. The live tier_participants row is still
returned as `current_participation` for comparison, but `participation`
(the snapshot) is what should be shown as "why".
"""

from database.db import get_conn


def explain_distribution(distribution_id):
    with get_conn() as conn:
        d = conn.execute("SELECT * FROM distributions WHERE id=?", (distribution_id,)).fetchone()
        if not d:
            return None
        event = conn.execute("SELECT * FROM revenue_events WHERE id=?", (d["revenue_event_id"],)).fetchone()
        tier = conn.execute("SELECT * FROM waterfall_tiers WHERE id=?", (d["tier_id"],)).fetchone()
        stakeholder = conn.execute("SELECT * FROM stakeholders WHERE id=?", (d["stakeholder_id"],)).fetchone()
        version = None
        if d["agreement_version_id"]:
            version = conn.execute(
                "SELECT * FROM agreement_versions WHERE id=?", (d["agreement_version_id"],)
            ).fetchone()
        current_participation = conn.execute(
            "SELECT * FROM tier_participants WHERE tier_id=? AND stakeholder_id=?",
            (d["tier_id"], d["stakeholder_id"]),
        ).fetchone()

        return {
            "distribution": dict(d),
            "revenue_event": dict(event) if event else None,
            "tier": dict(tier) if tier else None,
            "stakeholder": dict(stakeholder) if stakeholder else None,
            "agreement_version": dict(version) if version else None,
            # The immutable answer to "on what terms was this calculated" —
            # frozen on the distribution row itself.
            "participation": {
                "rule_type": d["rule_type"],
                "pool": d["pool"],
                "rate_pct": d["rate_pct"],
                "points": d["points"],
                "cap_cents": d["cap_cents"],
            },
            # The tier_participants row as it stands TODAY — may differ from
            # `participation` above if the assignment has been edited since.
            "current_participation": dict(current_participation) if current_participation else None,
        }


def list_distributions_for_stakeholder(project_id, stakeholder_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*, re.source, re.territory, re.entry_date AS revenue_date, wt.name AS tier_name
               FROM distributions d
               JOIN revenue_events re ON re.id = d.revenue_event_id
               JOIN waterfall_tiers wt ON wt.id = d.tier_id
               WHERE d.project_id=? AND d.stakeholder_id=?
               ORDER BY d.id""",
            (project_id, stakeholder_id),
        ).fetchall()
        return [dict(r) for r in rows]
