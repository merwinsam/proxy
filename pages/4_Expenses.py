import streamlit as st
from database.db import get_conn
from engine.expenses import (
    add_expense,
    list_expenses,
    approve_expense,
    disallow_expense,
    split_expense,
)
from money import to_cents, from_cents

st.set_page_config(page_title="Expenses", page_icon="🧾", layout="wide")
st.title("🧾 Expense Staging Queue")

active_id = st.session_state.get("active_project_id")
if not active_id:
    st.warning("Create or select a project on the Project Dashboard page first.")
    st.stop()

with get_conn() as conn:
    project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (active_id,)).fetchone())

st.caption(f"Reviewing expenses for **{project['proxy_id']} — {project['title']}**")
st.info(
    "Pending transactions never touch the waterfall. Approving an expense authorizes it "
    "(ALLOCATED) but doesn't move any ledger money by itself — it only becomes a real cash "
    "outflow once a revenue event's expense-cap tier actually reimburses it."
)

with st.expander("➕ Log a new card transaction"):
    with st.form("add_expense_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        vendor = c1.text_input("Vendor")
        amount = c2.number_input("Amount ($)", min_value=0.0, step=10.0)
        description = st.text_input("Description")
        c3, c4 = st.columns(2)
        card_holder = c3.text_input("Card holder")
        card_last4 = c4.text_input("Card last 4 digits", max_chars=4)
        if st.form_submit_button("Log transaction"):
            if not vendor or amount <= 0:
                st.error("Vendor and a positive amount are required.")
            else:
                add_expense(active_id, vendor, to_cents(amount), description, card_holder, card_last4)
                st.success(f"Logged ${amount:,.2f} at {vendor} as PENDING_REVIEW")
                st.rerun()

st.divider()

pending = list_expenses(active_id, status="PENDING_REVIEW")
pending_total_cents = sum(e["amount_cents"] for e in pending)

st.subheader(f"Expenses needing review — ${from_cents(pending_total_cents):,.2f}")

if not pending:
    st.caption("Nothing pending review.")

for e in pending:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        c1.markdown(f"**{e['vendor']}** — ${from_cents(e['amount_cents']):,.2f}")
        c1.caption(f"{e['description'] or 'No description'}  \nCard: {e['card_holder']} •••• {e['card_last4']}")

        b1, b2, b3 = c2.columns(3)
        if b1.button("✅", key=f"approve_{e['id']}", help="Approve"):
            approve_expense(e["id"])
            st.rerun()
        if b2.button("❌", key=f"disallow_{e['id']}", help="Disallow"):
            disallow_expense(e["id"])
            st.rerun()
        if b3.button("✂️", key=f"split_toggle_{e['id']}", help="Split"):
            st.session_state[f"splitting_{e['id']}"] = True

        if st.session_state.get(f"splitting_{e['id']}"):
            st.markdown("**Split this expense into two:**")
            sc1, sc2 = st.columns(2)
            amt_a = sc1.number_input(
                "Amount A", min_value=0.0, max_value=from_cents(e["amount_cents"]),
                value=round(from_cents(e["amount_cents"]) / 2, 2), key=f"split_a_amt_{e['id']}",
            )
            desc_a = sc1.text_input("Description A", key=f"split_a_desc_{e['id']}")
            amt_b = from_cents(e["amount_cents"]) - amt_a
            sc2.metric("Amount B", f"${amt_b:,.2f}")
            desc_b = sc2.text_input("Description B", key=f"split_b_desc_{e['id']}")

            if st.button("Confirm split", key=f"confirm_split_{e['id']}"):
                amt_a_cents = to_cents(amt_a)
                amt_b_cents = e["amount_cents"] - amt_a_cents  # exact remainder, no rounding drift
                split_expense(e["id"], amt_a_cents, amt_b_cents, desc_a, desc_b)
                st.session_state[f"splitting_{e['id']}"] = False
                st.rerun()

st.divider()

col_a, col_d = st.columns(2)

with col_a:
    st.subheader("✅ Allocated (approved)")
    allocated = list_expenses(active_id, status="ALLOCATED")
    if allocated:
        st.table(
            {
                "Vendor": [e["vendor"] for e in allocated],
                "Amount": [f"${from_cents(e['amount_cents']):,.2f}" for e in allocated],
                "Description": [e["description"] or "" for e in allocated],
            }
        )
    else:
        st.caption("None yet.")

with col_d:
    st.subheader("❌ Disallowed")
    disallowed = list_expenses(active_id, status="DISALLOWED")
    if disallowed:
        st.table(
            {
                "Vendor": [e["vendor"] for e in disallowed],
                "Amount": [f"${from_cents(e['amount_cents']):,.2f}" for e in disallowed],
                "Description": [e["description"] or "" for e in disallowed],
            }
        )
    else:
        st.caption("None yet.")
