"""
Rights Registry — agreement versions.

Every stakeholder's contract terms (points, entitlement_type, investment,
premium, pool) are versioned as a FULL snapshot, not just points. Changing
any one field writes a new agreement_versions row carrying the complete new
state, then updates the stakeholders table's current/default values. That
gives a real history:

    Agreement v1   Effective Jan 1   Points: 15  Pool: Creative  ...
    Amendment #1   Effective Sep 3   Points: 20  Pool: Creative  ...
    Amendment #2   Effective Nov 12  Points: 22  Pool: Creative  ...

and lets a distribution point at exactly which version was in effect when
it was calculated (see engine/revenue.py). Resolution is by EFFECTIVE
DATE (current_agreement_version_id), not by which row was entered last —
an amendment dated in the future never applies to a distribution
calculated before that date arrives.
"""

from database.db import get_conn, today


def create_stakeholder_with_initial_version(
    conn, project_id, creator_id, name, role, points, entitlement_type,
    investment_amount_cents, premium_pct, pool, kyc_status, effective_date=None, note="Initial agreement"
):
    """Insert a stakeholder AND its v1 agreement_versions row, in the given connection/transaction."""
    cur = conn.execute(
        """INSERT INTO stakeholders
           (project_id, creator_id, name, role, points, entitlement_type,
            investment_amount_cents, premium_pct, pool, kyc_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, creator_id, name, role, points, entitlement_type,
         investment_amount_cents, premium_pct, pool, kyc_status),
    )
    stakeholder_id = cur.lastrowid
    conn.execute(
        """INSERT INTO agreement_versions
           (stakeholder_id, effective_date, points, entitlement_type,
            investment_amount_cents, premium_pct, pool, note, created_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (stakeholder_id, effective_date or today(), points, entitlement_type,
         investment_amount_cents, premium_pct, pool, note, today()),
    )
    return stakeholder_id


def amend_agreement(stakeholder_id, effective_date, note, **changed_fields):
    """
    changed_fields: any subset of points, entitlement_type,
    investment_amount_cents, premium_pct, pool. Unspecified fields carry
    forward from the current row. Writes a full-snapshot version row, then
    updates the stakeholder's current values to match.

    If investment_amount_cents actually changes, every recoupment tier this
    stakeholder participates in gets its target REBASED (adjusted by the
    delta, paid_to_date preserved) rather than left stale — see
    engine/tier_progress.py.
    """
    investment_changed = False
    with get_conn() as conn:
        current = dict(conn.execute("SELECT * FROM stakeholders WHERE id=?", (stakeholder_id,)).fetchone())
        new_state = {
            "points": current["points"],
            "entitlement_type": current["entitlement_type"],
            "investment_amount_cents": current["investment_amount_cents"],
            "premium_pct": current["premium_pct"],
            "pool": current["pool"],
        }
        new_state.update(changed_fields)
        investment_changed = new_state["investment_amount_cents"] != current["investment_amount_cents"]

        conn.execute(
            """INSERT INTO agreement_versions
               (stakeholder_id, effective_date, points, entitlement_type,
                investment_amount_cents, premium_pct, pool, note, created_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stakeholder_id, effective_date, new_state["points"], new_state["entitlement_type"],
                new_state["investment_amount_cents"], new_state["premium_pct"], new_state["pool"],
                note, today(),
            ),
        )
        conn.execute(
            """UPDATE stakeholders
               SET points=?, entitlement_type=?, investment_amount_cents=?, premium_pct=?, pool=?
               WHERE id=?""",
            (new_state["points"], new_state["entitlement_type"], new_state["investment_amount_cents"],
             new_state["premium_pct"], new_state["pool"], stakeholder_id),
        )

    if investment_changed:
        # Deferred import to avoid a circular import — tier_progress imports
        # tier_participants, not registry, so this direction is safe.
        from engine import tier_progress as tp
        tp.rebase_stakeholder_recoupment_targets(
            stakeholder_id, f"Investment amount amended — {note}", effective_date
        )


def list_agreement_versions(stakeholder_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agreement_versions WHERE stakeholder_id=? ORDER BY id",
            (stakeholder_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def current_agreement_version_id(conn, stakeholder_id, as_of_date=None):
    """
    The version in effect AS OF a given date — resolved by effective_date,
    not insertion order. A version dated in the future never leaks into a
    distribution calculated before it takes effect.

    Falls back to the earliest version on record if every version's
    effective_date is after as_of_date (e.g. the stakeholder's only
    version was entered with a future date by mistake) — there is always
    SOME agreement in force, even if it's not the one the caller expected.
    """
    as_of_date = as_of_date or today()
    row = conn.execute(
        """SELECT id FROM agreement_versions
           WHERE stakeholder_id=? AND effective_date <= ?
           ORDER BY effective_date DESC, id DESC LIMIT 1""",
        (stakeholder_id, as_of_date),
    ).fetchone()
    if row:
        return row["id"]
    fallback = conn.execute(
        "SELECT id FROM agreement_versions WHERE stakeholder_id=? ORDER BY effective_date ASC, id ASC LIMIT 1",
        (stakeholder_id,),
    ).fetchone()
    return fallback["id"] if fallback else None
