"""
Ledger.

Append-only project ledger. Historic entries are never deleted or edited —
errors are corrected with an offsetting entry, so the full history always
reconciles.

Revenue processing writes its own ledger entries directly inside
engine/revenue.py (one transaction, one owner) — this module only exposes
reads and manual corrections, so there's exactly one code path that can
create a 'revenue' entry.
"""

from database.db import get_conn, today


def add_correction(project_id, description, amount_cents, related_id=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ledger (project_id, entry_date, entry_type, description, amount_cents, related_id)
               VALUES (?, ?, 'correction', ?, ?, ?)""",
            (project_id, today(), description, amount_cents, related_id),
        )


def list_entries(project_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ledger WHERE project_id=? ORDER BY id", (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]
