import streamlit as st
from database.db import get_conn
from engine.revenue import process_revenue_event, process_held_cash
from engine.tier_participants import GrossPercentageOverflow
from money import to_cents, from_cents

st.set_page_config(page_title="Revenue Simulator", page_icon="💵", layout="wide")
st.title("💵 Revenue Simulator")

active_id = st.session_state.get("active_project_id")
if not active_id:
    st.warning("Create or select a project on the Project Dashboard page first.")
    st.stop()

with get_conn() as conn:
    project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (active_id,)).fetchone())
    tiers = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM waterfall_tiers WHERE project_id=? ORDER BY tier_number", (active_id,)
        ).fetchall()
    ]
    stakeholders = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM stakeholders WHERE project_id=? ORDER BY id", (active_id,)
        ).fetchall()
    ]

st.caption(f"Simulating revenue for **{project['proxy_id']} — {project['title']}**")

if not tiers:
    st.warning("Define waterfall tiers on the Waterfall Builder page before simulating revenue.")
    st.stop()

st.info(
    "Each run here is its own revenue event and its own payout batch — and every capped tier "
    "only pays its **remaining** entitlement, not its full target again. Try processing the "
    "same amount twice to see recouped investors stop receiving recoupment once they're paid off."
)

with st.form("simulate_revenue_form"):
    c1, c2, c3 = st.columns(3)
    source = c1.selectbox(
        "Revenue source",
        ["Netflix License", "Theatrical Box Office", "VOD / TVOD", "Foreign Sale", "Merchandising", "Other"],
    )
    amount = c2.number_input("Amount ($)", min_value=0.0, step=5000.0, value=150000.0)
    territory = c3.selectbox("Territory", ["United States", "United Kingdom", "Worldwide", "EU", "Other"])
    submitted = st.form_submit_button("Process Revenue", type="primary")

if submitted and amount > 0:
    try:
        result, revenue_event_id = process_revenue_event(active_id, source, territory, to_cents(amount))
    except GrossPercentageOverflow as e:
        st.error(f"⚠ Cannot process revenue — the contract itself is invalid: {e}")
        st.info("Fix the gross-participation rates on the Waterfall Builder page, then try again.")
        st.stop()
    st.session_state["last_waterfall_result"] = result
    st.session_state["last_revenue_event_id"] = revenue_event_id
    st.success(f"Processed ${amount:,.0f} from {source} ({territory}) — revenue event #{revenue_event_id}.")
    st.rerun()

st.divider()

held_cents = project["distributable_cash_cents"]
if held_cents > 0:
    st.warning(
        f"**${from_cents(held_cents):,.2f} is currently held** as distributable cash — money "
        f"already received that a prior event couldn't fully allocate (e.g. before a tier existed "
        f"to catch it). This is NOT new revenue; it stays out of revenue_received_cents."
    )
    if st.button("↻ Process available project cash"):
        try:
            result, revenue_event_id = process_held_cash(active_id)
        except GrossPercentageOverflow as e:
            st.error(f"⚠ Cannot process held cash — the contract itself is invalid: {e}")
            st.stop()
        if result is None:
            st.info("Nothing to process.")
        else:
            st.session_state["last_waterfall_result"] = result
            st.session_state["last_revenue_event_id"] = revenue_event_id
            st.success(f"Reprocessed ${from_cents(result.revenue_cents):,.0f} of held cash — revenue event #{revenue_event_id}.")
            st.rerun()

result = st.session_state.get("last_waterfall_result")
if result:
    st.markdown(f"## ${from_cents(result.revenue_cents):,.0f} RECEIVED")
    st.caption(f"Revenue event #{st.session_state.get('last_revenue_event_id')}")

    rows = []
    for tier in result.tier_results:
        label = {
            "expense_cap": "Distribution Expenses",
            "recoupment": "Investor Recoupment",
            "gross_percentage": "Gross Participation",
            "profit_split": "Net Profits (split across pools)",
        }.get(tier.tier_type, tier.name)
        rows.append((f"{label} — {tier.name}", tier.paid_this_run_cents, tier.remaining_after_cents))

    st.table(
        {
            "Tier": [r[0] for r in rows],
            "Paid this event": [f"${from_cents(r[1]):,.0f}" for r in rows],
            "Remaining balance": [f"${from_cents(r[2]):,.0f}" for r in rows],
        }
    )
    total_allocated = sum(r[1] for r in rows)
    st.caption(
        f"Total allocated across tiers: ${from_cents(total_allocated):,.0f} "
        f"(residual to distributable cash: ${from_cents(result.residual_cents):,.2f})"
    )

    st.markdown("### RIGHTSHOLDER DISTRIBUTION")
    dist_rows = [
        (
            next(s["name"] for s in stakeholders if s["id"] == sid),
            next(s["role"] for s in stakeholders if s["id"] == sid),
            amt,
        )
        for sid, amt in result.per_stakeholder_total.items()
        if amt > 0
    ]
    if dist_rows:
        st.table(
            {
                "Stakeholder": [r[0] for r in dist_rows],
                "Role": [r[1] for r in dist_rows],
                "Amount": [f"${from_cents(r[2]):,.2f}" for r in dist_rows],
            }
        )
    else:
        st.caption("No one had a remaining entitlement at this point — all balances already satisfied.")
    st.caption("Head to the Audit Ledger page to see this recorded as ledger entries and a payout batch.")
else:
    st.caption("Process a revenue event above to see the waterfall run.")
