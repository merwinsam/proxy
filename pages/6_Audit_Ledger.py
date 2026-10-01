import streamlit as st
from database.db import get_conn
from engine.ledger import list_entries, add_correction
from engine.payouts import list_payouts, simulate_payout
from engine.provenance import explain_distribution
from money import to_cents, from_cents

st.set_page_config(page_title="Audit Ledger", page_icon="📒", layout="wide")
st.title("📒 Project Ledger & Payouts")

active_id = st.session_state.get("active_project_id")
if not active_id:
    st.warning("Create or select a project on the Project Dashboard page first.")
    st.stop()

with get_conn() as conn:
    project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (active_id,)).fetchone())

st.caption(f"Ledger for **{project['proxy_id']} — {project['title']}**")

tab_ledger, tab_payouts = st.tabs(["Ledger", "Payouts"])

ENTRY_ICONS = {
    "revenue": "💵",
    "expense_payment": "🧾",
    "tier_info": "🌊",
    "distribution": "➡️",
    "correction": "↩️",
    "target_adjustment": "🔧",
    "held_cash_sweep": "🔁",
}

with tab_ledger:
    entries = list_entries(active_id)

    if not entries:
        st.info("No ledger activity yet. Process revenue on the Revenue page to populate this.")
    else:
        st.subheader("PROJECT LEDGER")
        for e in entries:
            icon = ENTRY_ICONS.get(e["entry_type"], "•")
            if e["entry_type"] in ("tier_info", "target_adjustment"):
                # Zero-dollar informational marker — no +/- sign, not part of the balance.
                st.caption(f"`{e['entry_date']}`  {icon}  {e['description']}")
            else:
                sign = "+" if e["amount_cents"] >= 0 else "-"
                st.markdown(
                    f"`{e['entry_date']}`  {icon}  **{e['entry_type'].replace('_', ' ').title()}**"
                    f"  —  {e['description']}  —  {sign}${from_cents(abs(e['amount_cents'])):,.2f}"
                )

        real_entries = [e for e in entries if e["entry_type"] != "tier_info"]
        running_total = sum(e["amount_cents"] for e in real_entries)
        st.divider()
        st.metric("Net ledger balance (should reconcile to $0 once fully distributed)", f"${from_cents(running_total):,.2f}")

    st.divider()
    with st.expander("↩️ Post a correction (never delete history)"):
        with st.form("correction_form", clear_on_submit=True):
            desc = st.text_input("Description", placeholder="e.g. Reimbursed erroneous B&H Photo charge")
            amt = st.number_input(
                "Amount ($) — positive to credit the project back, negative to debit",
                step=100.0,
            )
            if st.form_submit_button("Post correction"):
                if desc:
                    add_correction(active_id, desc, to_cents(amt))
                    st.success("Correction posted.")
                    st.rerun()
                else:
                    st.error("Add a description first.")

with tab_payouts:
    payouts = list_payouts(active_id)
    if not payouts:
        st.info("No distributions recorded yet — process revenue on the Revenue page first.")
    else:
        st.subheader("PAYOUT BATCHES")
        st.caption("One batch per revenue event per stakeholder — paying one batch never touches another.")

        by_event = {}
        for p in payouts:
            by_event.setdefault(p["revenue_event_id"], []).append(p)

        for event_id, rows in sorted(by_event.items()):
            first = rows[0]
            with st.container(border=True):
                st.markdown(f"**Revenue event #{event_id}** — {first['source']} ({first['territory']}), {first['revenue_date']}")
                for p in rows:
                    c1, c2, c3 = st.columns([2, 1, 1])
                    icon = "✓" if p["status"] in ("READY", "PAID") else "⚠"
                    c1.markdown(f"{icon} **{p['name']}**  \n{p['role']} · `{p['creator_id']}`")
                    c2.markdown(f"**${from_cents(p['amount_cents']):,.2f}**")
                    if p["status"] == "PAID":
                        c3.success("PAID (simulated)")
                    elif p["status"] == "READY":
                        if c3.button("Simulate Payout", key=f"payout_{p['id']}"):
                            simulate_payout(p["id"])
                            st.rerun()
                    else:
                        c3.warning("KYC REQUIRED")

                    with st.expander(f"❓ Why did {p['name']} receive ${from_cents(p['amount_cents']):,.2f}?"):
                        with get_conn() as conn:
                            dist_rows = [
                                dict(r) for r in conn.execute(
                                    """SELECT d.*, wt.name AS tier_name, wt.tier_number
                                       FROM distributions d JOIN waterfall_tiers wt ON wt.id = d.tier_id
                                       WHERE d.stakeholder_id=? AND d.revenue_event_id=?
                                       ORDER BY wt.tier_number""",
                                    (p["stakeholder_id"], p["revenue_event_id"]),
                                ).fetchall()
                            ]
                        st.markdown(f"**Revenue event #{p['revenue_event_id']}** — {p['source']} ({p['territory']})")
                        for d in dist_rows:
                            explanation = explain_distribution(d["id"])
                            version = explanation["agreement_version"]
                            line = f"↳ Tier {d['tier_number']} — {d['tier_name']}: **${from_cents(d['amount_cents']):,.2f}**"
                            if version:
                                line += (
                                    f"  \n&nbsp;&nbsp;&nbsp;&nbsp;under agreement effective `{version['effective_date']}` "
                                    f"({version['points']:g} pts, {version['pool']} pool)"
                                )
                            st.markdown(line)

        st.caption(
            "No real money moves here — this represents where Stripe Connect payouts, "
            "KYC checks and ACH transfers would plug in for a production system."
        )
