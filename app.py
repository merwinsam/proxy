import streamlit as st
from database.db import init_db, get_conn, generate_creator_id, today
from engine import tier_progress as tp
from engine.registry import create_stakeholder_with_initial_version
from engine.tier_participants import add_participant_in_conn
from engine.payouts import outstanding_total_cents
from money import from_cents, to_cents

st.set_page_config(page_title="Proxy", page_icon="🎬", layout="wide")

init_db()


def seed_demo_project_if_empty():
    """Seed 'THE LAST SIGNAL' demo project on first run, mirroring the spec's example."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
        if count > 0:
            return

        cur = conn.execute(
            """INSERT INTO projects (proxy_id, title, project_type, producer, budget_cents,
                                      revenue_received_cents, distributable_cash_cents, created_on)
               VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
            ("PROXY-000001", "THE LAST SIGNAL", "Feature Film", "Studio LLC", to_cents(500000), today()),
        )
        project_id = cur.lastrowid

        # name, role, points (profit-pool weight), entitlement_type (default/descriptive),
        # investment($), premium_pct (default), pool (default)
        stakeholders = [
            ("Sarah Chen", "Investor", 62.5, "recoupment_plus_premium", 50000, 20, "investor"),
            ("Marcus Lee", "Investor", 37.5, "recoupment_plus_premium", 30000, 20, "investor"),
            ("James Cole", "Director", 20, "continuing_participation", 0, 0, "creative"),
            ("Anna Rao", "Writer", 15, "continuing_participation", 0, 0, "creative"),
            ("Studio LLC", "Producer", 65, "continuing_participation", 0, 0, "creative"),
        ]
        sid = {}
        for name, role, points, ent_type, investment, premium, pool in stakeholders:
            sid[name] = create_stakeholder_with_initial_version(
                conn, project_id, generate_creator_id(), name, role, points, ent_type,
                to_cents(investment), premium, pool,
                "READY" if role != "Producer" else "KYC_REQUIRED",
                note="Initial Rights Agreement",
            )

        # A second version for Anna, to show the amendment history working from day one.
        conn.execute(
            """INSERT INTO agreement_versions
               (stakeholder_id, effective_date, points, entitlement_type,
                investment_amount_cents, premium_pct, pool, note, created_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid["Anna Rao"], today(), 15, "continuing_participation", 0, 0, "creative",
             "Amendment #1 — screenplay v3 delivered, creative points confirmed", today()),
        )

        tiers = [
            (1, "Production / Distribution Expenses", "expense_cap", 20000, 0, 0),
            (2, "Investor Recoupment", "recoupment", 0, 0, 0),
            (3, "Net Profits", "profit_split", 0, 50, 50),
        ]
        tid = {}
        for tier_number, name, tier_type, cap, inv_pct, cre_pct in tiers:
            c = conn.execute(
                """INSERT INTO waterfall_tiers
                   (project_id, tier_number, name, tier_type, cap_amount_cents,
                    investor_pool_pct, creative_pool_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (project_id, tier_number, name, tier_type, to_cents(cap), inv_pct, cre_pct),
            )
            tid[name] = c.lastrowid

        # The contract, explicit: who belongs in which tier, on what terms.
        add_participant_in_conn(conn, tid["Investor Recoupment"], sid["Sarah Chen"], "recoupment_premium", rate_pct=20)
        add_participant_in_conn(conn, tid["Investor Recoupment"], sid["Marcus Lee"], "recoupment_premium", rate_pct=20)

        add_participant_in_conn(conn, tid["Net Profits"], sid["Sarah Chen"], "profit_points", pool="investor", points=62.5)
        add_participant_in_conn(conn, tid["Net Profits"], sid["Marcus Lee"], "profit_points", pool="investor", points=37.5)
        add_participant_in_conn(conn, tid["Net Profits"], sid["James Cole"], "profit_points", pool="creative", points=20)
        add_participant_in_conn(conn, tid["Net Profits"], sid["Anna Rao"], "profit_points", pool="creative", points=15)
        add_participant_in_conn(conn, tid["Net Profits"], sid["Studio LLC"], "profit_points", pool="creative", points=65)

        demo_expenses = [
            ("B&H Photo", 4820, "Camera equipment", "Sarah", "4821"),
            ("Metro Catering", 1240, "Crew catering", "Michael", "1202"),
            ("Amazon", 379, "Unknown purchase", "Jacob", "8821"),
            ("Legal Associates LLP", 5000, "Legal fees", "Studio LLC", "9010"),
            ("Sales Agent Co.", 15000, "Sales agent commission", "Studio LLC", "9010"),
        ]
        for vendor, amount, desc, holder, last4 in demo_expenses:
            conn.execute(
                """INSERT INTO expenses
                   (project_id, vendor, amount_cents, description, card_holder, card_last4, status, created_on)
                   VALUES (?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?)""",
                (project_id, vendor, to_cents(amount), desc, holder, last4, today()),
            )

        tiers_full = [dict(r) for r in conn.execute(
            "SELECT * FROM waterfall_tiers WHERE project_id=?", (project_id,)
        ).fetchall()]
        tp.ensure_progress_rows(conn, project_id, tiers_full)


seed_demo_project_if_empty()


def get_all_projects():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()]


st.sidebar.title("🎬 Proxy")
st.sidebar.caption("Rights, revenue & waterfall prototype")

projects = get_all_projects()
project_labels = {p["id"]: f"{p['proxy_id']} — {p['title']}" for p in projects}

if "active_project_id" not in st.session_state:
    st.session_state.active_project_id = projects[0]["id"] if projects else None

selected_id = st.sidebar.selectbox(
    "Active project",
    options=list(project_labels.keys()),
    format_func=lambda pid: project_labels[pid],
    index=list(project_labels.keys()).index(st.session_state.active_project_id)
    if st.session_state.active_project_id in project_labels
    else 0,
)
st.session_state.active_project_id = selected_id

st.sidebar.divider()
st.sidebar.markdown(
    "**Prototype scope**\n\n"
    "✅ Contract-driven tiers (explicit participants), stateful entitlement balances, "
    "versioned agreements, reconciling ledger, batched simulated payouts\n\n"
    "🚫 Real Stripe transfers, KYC, ACH, tax docs — simulated only"
)

st.title("Proxy — Prototype 1")
st.markdown(
    """
Welcome to the Proxy prototype dashboard. Use the pages in the sidebar to walk the full flow:

1. **Project Dashboard** — create/view a project
2. **Rights Registry** — add stakeholders, assign Creator IDs, and amend their agreement over time
3. **Waterfall Builder** — define tiers and assign SPECIFIC participants to each, on their own terms
4. **Expenses** — approve, disallow or split pending card transactions
5. **Revenue** — simulate incoming revenue and run the stateful, contract-driven waterfall
6. **Audit Ledger** — the reconciling transaction history, batched payouts, and per-distribution "why"

A demo project, **THE LAST SIGNAL**, is pre-loaded with the numbers from the spec — now with
Sarah Chen and Marcus Lee explicitly assigned to the recoupment tier (not just "every investor"),
and Anna Rao's Rights Registry entry carrying two agreement versions.
"""
)

active = next((p for p in projects if p["id"] == selected_id), None)
if active:
    outstanding = outstanding_total_cents(active["id"])
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Project", active["proxy_id"])
    col2.metric("Budget", f"${from_cents(active['budget_cents']):,.0f}")
    col3.metric("Revenue received", f"${from_cents(active['revenue_received_cents']):,.0f}")
    col4.metric("Outstanding payouts", f"${from_cents(outstanding):,.0f}")
