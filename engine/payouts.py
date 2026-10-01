"""
Simulated Payouts.

Each processed revenue event creates its own payout batch per stakeholder
(see engine/revenue.py) — there is no aggregate "sum of all distributions"
anywhere, so marking one batch PAID can never change the amount owed on a
different batch. "Simulate Payout" just flips one batch's status; no money
moves.
"""

from database.db import get_conn


def list_payouts(project_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.*, s.name, s.role, s.creator_id, re.source, re.territory, re.entry_date AS revenue_date
               FROM payouts p
               JOIN stakeholders s ON s.id = p.stakeholder_id
               JOIN revenue_events re ON re.id = p.revenue_event_id
               WHERE s.project_id = ?
               ORDER BY p.id""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def simulate_payout(payout_id):
    with get_conn() as conn:
        payout = conn.execute("SELECT * FROM payouts WHERE id=?", (payout_id,)).fetchone()
        if not payout or payout["status"] != "READY":
            return False
        conn.execute("UPDATE payouts SET status='PAID' WHERE id=?", (payout_id,))
        return True


def outstanding_total_cents(project_id):
    """Sum of everything not yet marked PAID — a true, un-inflatable total."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(p.amount_cents), 0) AS t
               FROM payouts p JOIN stakeholders s ON s.id = p.stakeholder_id
               WHERE s.project_id=? AND p.status != 'PAID'""",
            (project_id,),
        ).fetchone()
        return row["t"]
