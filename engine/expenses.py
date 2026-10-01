"""
Expense Staging Engine.

Card transactions land as PENDING_REVIEW and do NOT affect the waterfall
until an accountant explicitly approves (ALLOCATED) or rejects (DISALLOWED)
them.

Note: approving an expense does NOT write a ledger entry. Approval just
authorizes the cost — it becomes a real cash outflow only when a revenue
event's expense_cap tier actually reimburses it (see engine/revenue.py),
which is where the single "Expense payments" ledger line is written. This
avoids the double-counting bug where approval-time and reimbursement-time
entries both hit the ledger for the same money.
"""

from database.db import get_conn, today


def add_expense(project_id, vendor, amount_cents, description, card_holder, card_last4):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO expenses
               (project_id, vendor, amount_cents, description, card_holder, card_last4, status, created_on)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?)""",
            (project_id, vendor, amount_cents, description, card_holder, card_last4, today()),
        )


def list_expenses(project_id, status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE project_id=? AND status=? ORDER BY id",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def approve_expense(expense_id):
    with get_conn() as conn:
        conn.execute("UPDATE expenses SET status='ALLOCATED' WHERE id=?", (expense_id,))


def disallow_expense(expense_id):
    with get_conn() as conn:
        conn.execute("UPDATE expenses SET status='DISALLOWED' WHERE id=?", (expense_id,))


def split_expense(expense_id, amount_a_cents, amount_b_cents, description_a, description_b):
    """Split one pending expense into two new pending expenses; original is disallowed."""
    with get_conn() as conn:
        expense = conn.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
        conn.execute("UPDATE expenses SET status='DISALLOWED' WHERE id=?", (expense_id,))
        for amt, desc in [(amount_a_cents, description_a), (amount_b_cents, description_b)]:
            conn.execute(
                """INSERT INTO expenses
                   (project_id, vendor, amount_cents, description, card_holder, card_last4, status, created_on)
                   VALUES (?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?)""",
                (
                    expense["project_id"],
                    expense["vendor"],
                    amt,
                    desc,
                    expense["card_holder"],
                    expense["card_last4"],
                    today(),
                ),
            )


def approved_total_cents(project_id):
    """Cumulative lifetime ALLOCATED total. The waterfall's expense_cap tier
    subtracts its own paid_to_date from this to find outstanding (approved
    but not yet reimbursed) expenses — see engine/waterfall.py."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total FROM expenses WHERE project_id=? AND status='ALLOCATED'",
            (project_id,),
        ).fetchone()
        return row["total"]
