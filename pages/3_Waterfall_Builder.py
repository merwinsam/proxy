import streamlit as st
from database.db import get_conn
from engine import waterfall as wf_engine
from engine import tier_progress as tp
from engine.tier_progress import list_adjustments
from engine.expenses import approved_total_cents
from engine.tier_participants import (
    add_participant, update_participant, remove_participant, list_participants,
    list_participants_for_project, GrossPercentageOverflow,
)
from money import to_cents, from_cents

st.set_page_config(page_title="Waterfall Builder", page_icon="🌊", layout="wide")
st.title("🌊 Waterfall Builder")

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

st.caption(f"Configuring tiers for **{project['proxy_id']} — {project['title']}**")

TIER_TYPE_LABELS = {
    "expense_cap": "Expense cap",
    "recoupment": "Recoupment (+ premium)",
    "gross_percentage": "Gross participation (off the top)",
    "profit_split": "Net profit split",
}

with st.expander("➕ Add a tier"):
    with st.form("add_tier_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Tier name", placeholder="e.g. Sales Agent Fees")
        tier_type = c2.selectbox(
            "Tier type", ["expense_cap", "recoupment", "gross_percentage", "profit_split"],
            format_func=lambda x: TIER_TYPE_LABELS[x],
        )
        next_number = (max((t["tier_number"] for t in tiers), default=0)) + 1

        cap_amount = 0.0
        investor_pct = 50.0
        if tier_type == "expense_cap":
            cap_amount = st.number_input("Cap amount ($)", min_value=0.0, step=1000.0)
        elif tier_type == "profit_split":
            investor_pct = st.slider(
                "Investor pool %", min_value=0, max_value=100, value=50,
                help="Creative pool always gets exactly the remainder — there's no separate "
                     "creative % input, so the two can never disagree.",
            )
            st.caption(f"Creative pool: **{100 - investor_pct}%** (always the remainder — not independently settable)")

        if st.form_submit_button("Add tier"):
            if not name:
                st.error("Name the tier first.")
            else:
                with get_conn() as conn:
                    conn.execute(
                        """INSERT INTO waterfall_tiers
                           (project_id, tier_number, name, tier_type, cap_amount_cents,
                            investor_pool_pct, creative_pool_pct)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (active_id, next_number, name, tier_type, to_cents(cap_amount),
                         investor_pct, 100 - investor_pct),
                    )
                st.success(f"Added Tier {next_number}: {name}")
                st.rerun()

st.divider()

if not tiers:
    st.info("No tiers defined yet — add expense, recoupment and profit-split tiers above.")
    st.stop()

if not stakeholders:
    st.warning("Add stakeholders on the Rights Registry page before assigning tier participants.")

# --- Tier participants: the explicit contract ---
st.subheader("Tier participants — who belongs in each tier, and on what terms")
st.caption(
    "This is the contract. A stakeholder not assigned to a tier is never touched by it, "
    "even if their Rights Registry defaults suggest they'd normally qualify."
)

RULE_LABELS = {
    "recoupment_fixed": "Fixed recoupment (no premium)",
    "recoupment_premium": "Recoupment + premium",
    "gross_percentage": "Gross % (off the top)",
    "profit_points": "Profit-split points",
}
TIER_TYPE_TO_RULE = {
    "recoupment": ["recoupment_fixed", "recoupment_premium"],
    "gross_percentage": ["gross_percentage"],
    "profit_split": ["profit_points"],
}

for tier in tiers:
    with st.container(border=True):
        st.markdown(f"**Tier {tier['tier_number']} — {tier['name']}**  ({TIER_TYPE_LABELS[tier['tier_type']]})")

        if tier["tier_type"] == "expense_cap":
            st.caption(f"Cap: ${from_cents(tier['cap_amount_cents']):,.0f}. Expense-cap tiers pay vendors, not individual stakeholders — no participants needed.")
            continue

        participants = list_participants(tier["id"])
        if participants:
            for p in participants:
                pc1, pc2, pc3 = st.columns([2, 2, 1])
                detail = ""
                if p["rule_type"] == "recoupment_premium":
                    detail = f"{p['rate_pct']:g}% premium"
                elif p["rule_type"] == "recoupment_fixed":
                    detail = "no premium"
                elif p["rule_type"] == "gross_percentage":
                    detail = f"{p['rate_pct']:g}% of gross"
                elif p["rule_type"] == "profit_points":
                    detail = f"{p['pool']} pool · {p['points']:g} points"
                pc1.markdown(f"**{p['stakeholder_name']}**  \n`{p['creator_id']}`")
                pc2.markdown(f"{RULE_LABELS.get(p['rule_type'], p['rule_type'])}  \n{detail}")
                if pc3.button("Remove", key=f"remove_participant_{p['id']}"):
                    remove_participant(p["id"])
                    st.rerun()

                with st.expander(f"✏️ Edit {p['stakeholder_name']}'s terms on this tier"):
                    st.caption(
                        "Changing a recoupment rate here REBASES the stakeholder's remaining "
                        "target — it adjusts by the difference and logs the amendment; it never "
                        "silently overwrites what's already been paid."
                    )
                    with st.form(f"edit_participant_{p['id']}"):
                        if p["rule_type"] == "recoupment_premium":
                            new_rate = st.number_input(
                                "Premium (%)", min_value=0.0, step=1.0,
                                value=float(p["rate_pct"] or 0), key=f"edit_rate_{p['id']}",
                            )
                            new_points, new_pool = None, None
                        elif p["rule_type"] == "gross_percentage":
                            new_rate = st.number_input(
                                "% of gross revenue", min_value=0.0, max_value=100.0, step=1.0,
                                value=float(p["rate_pct"] or 0), key=f"edit_gross_{p['id']}",
                            )
                            new_points, new_pool = None, None
                        elif p["rule_type"] == "profit_points":
                            ec1, ec2 = st.columns(2)
                            new_pool = ec1.selectbox(
                                "Pool", ["investor", "creative"],
                                index=["investor", "creative"].index(p["pool"]) if p["pool"] in ("investor", "creative") else 0,
                                key=f"edit_pool_{p['id']}",
                            )
                            new_points = ec2.number_input(
                                "Points", min_value=0.0, step=0.5,
                                value=float(p["points"] or 0), key=f"edit_points_{p['id']}",
                            )
                            new_rate = None
                        else:  # recoupment_fixed — nothing to edit but the cap
                            new_rate, new_points, new_pool = None, None, None

                        note = st.text_input("Note (why this is changing)", key=f"edit_note_{p['id']}")
                        effective = st.date_input("Effective date", key=f"edit_date_{p['id']}")

                        if st.form_submit_button("Save changes"):
                            try:
                                update_participant(
                                    p["id"], rate_pct=new_rate, points=new_points, pool=new_pool,
                                    note=note, effective_date=str(effective),
                                )
                                st.success("Updated — any recoupment target was rebased, not overwritten.")
                                st.rerun()
                            except GrossPercentageOverflow as e:
                                st.error(f"⚠ {e}")
        else:
            st.caption("No participants assigned yet.")

        available_rules = TIER_TYPE_TO_RULE.get(tier["tier_type"], [])
        assigned_ids = {p["stakeholder_id"] for p in participants}
        assignable = [s for s in stakeholders if s["id"] not in assigned_ids]

        if assignable and available_rules:
            with st.form(f"assign_form_{tier['id']}", clear_on_submit=True):
                ac1, ac2 = st.columns(2)
                chosen = ac1.selectbox(
                    "Add participant", assignable, format_func=lambda s: s["name"], key=f"assign_who_{tier['id']}"
                )
                rule_type = ac2.selectbox(
                    "Rule", available_rules, format_func=lambda r: RULE_LABELS[r], key=f"assign_rule_{tier['id']}"
                )

                rate_pct = None
                points = None
                pool = None
                if rule_type == "recoupment_premium":
                    rate_pct = st.number_input(
                        "Premium (%)", min_value=0.0, step=1.0,
                        value=float(chosen["premium_pct"]) if chosen else 0.0, key=f"assign_rate_{tier['id']}",
                    )
                elif rule_type == "gross_percentage":
                    rate_pct = st.number_input(
                        "% of gross revenue", min_value=0.0, max_value=100.0, step=1.0,
                        value=float(chosen["points"]) if chosen and chosen["entitlement_type"] == "percentage" else 0.0,
                        key=f"assign_gross_{tier['id']}",
                    )
                elif rule_type == "profit_points":
                    pc1, pc2 = st.columns(2)
                    pool = pc1.selectbox(
                        "Pool", ["investor", "creative"],
                        index=["investor", "creative"].index(chosen["pool"]) if chosen and chosen["pool"] in ("investor", "creative") else 0,
                        key=f"assign_pool_{tier['id']}",
                    )
                    points = pc2.number_input(
                        "Points", min_value=0.0, step=0.5,
                        value=float(chosen["points"]) if chosen else 0.0, key=f"assign_points_{tier['id']}",
                    )

                if st.form_submit_button("Assign to this tier"):
                    try:
                        add_participant(
                            tier["id"], chosen["id"], rule_type,
                            pool=pool, rate_pct=rate_pct, points=points,
                        )
                        st.success(f"{chosen['name']} assigned to Tier {tier['tier_number']}.")
                        st.rerun()
                    except GrossPercentageOverflow as e:
                        st.error(f"⚠ {e}")
        elif not assignable:
            st.caption("Every stakeholder is already assigned to this tier.")

st.divider()

# --- Current entitlement balances ---
st.subheader("Current entitlement balances")
st.caption("These persist across revenue events — this is what makes the waterfall stateful.")
with get_conn() as conn:
    tp.ensure_progress_rows(conn, active_id, tiers)
tier_level, stakeholder_level = tp.load_progress_for_preview(active_id, tiers)
stakeholder_names = {s["id"]: s["name"] for s in stakeholders}

for tier in tiers:
    if tier["tier_type"] == "expense_cap" and tier["id"] in tier_level:
        prog = tier_level[tier["id"]]
        remaining = max(0, (prog["target_cents"] or 0) - prog["paid_to_date_cents"])
        st.markdown(
            f"**Tier {tier['tier_number']} — {tier['name']}**: "
            f"${from_cents(prog['paid_to_date_cents']):,.0f} paid of "
            f"${from_cents(prog['target_cents']):,.0f} cap · **${from_cents(remaining):,.0f} remaining**"
        )
    elif tier["tier_type"] == "recoupment":
        rows = [(k, v) for k, v in stakeholder_level.items() if k[0] == tier["id"]]
        if rows:
            st.markdown(f"**Tier {tier['tier_number']} — {tier['name']}**")
            for (tid, sid), prog in rows:
                name = stakeholder_names.get(sid, "Unknown")
                remaining = max(0, (prog["target_cents"] or 0) - prog["paid_to_date_cents"])
                st.markdown(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;{name}: "
                    f"${from_cents(prog['paid_to_date_cents']):,.0f} paid of "
                    f"${from_cents(prog['target_cents']):,.0f} target · **${from_cents(remaining):,.0f} remaining**"
                )
                adjustments = list_adjustments(prog["row_id"])
                if adjustments:
                    for a in adjustments:
                        sign = "+" if a["delta_cents"] > 0 else ""
                        st.caption(
                            f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↳ `{a['effective_date']}` target {sign}"
                            f"${from_cents(a['delta_cents']):,.2f} ({a['note']})"
                        )

st.divider()

# --- Preview ---
st.subheader("Preview the cascade")
approved = approved_total_cents(active_id)
st.caption(f"Approved (ALLOCATED) expenses on hand for this project: **${from_cents(approved):,.0f}**")
preview_revenue = st.slider("Hypothetical incoming revenue ($)", min_value=0, max_value=500000, value=150000, step=5000)
st.caption("This preview does NOT persist — it shows what the NEXT event would pay given current balances and the current contract.")

with get_conn() as conn:
    participants_by_tier = list_participants_for_project(conn, active_id)

try:
    result = wf_engine.calculate(
        to_cents(preview_revenue), approved, tiers, participants_by_tier, tier_level, stakeholder_level
    )
except GrossPercentageOverflow as e:
    st.error(f"⚠ {e}")
    st.stop()

st.markdown(f"### 💰 ${preview_revenue:,.0f} enters the waterfall")

for tier in result.tier_results:
    target = tier.contractual_target_cents or 0
    pct = 0 if target == 0 else min(100, round(100 * (tier.paid_to_date_before_cents + tier.paid_this_run_cents) / target))
    with st.container(border=True):
        st.markdown(f"**TIER {tier.tier_number} — {tier.name}**  ({TIER_TYPE_LABELS.get(tier.tier_type, tier.tier_type)})")
        if tier.tier_type == "profit_split":
            # "target" here means "entered the tier" (uncapped) — what actually
            # reached stakeholders can be less, if one pool has no participants.
            st.caption(f"Entered this tier: ${from_cents(target):,.0f}")
            st.markdown(f"Distributed to stakeholders: **${from_cents(tier.paid_this_run_cents):,.0f}**")
            if tier.paid_this_run_cents < target:
                st.warning(
                    f"${from_cents(target - tier.paid_this_run_cents):,.0f} of this tier has no assigned "
                    f"participant on one side and falls through as project cash instead."
                )
        else:
            st.caption(
                f"Contractual target: ${from_cents(target):,.0f}  ·  "
                f"Already paid: ${from_cents(tier.paid_to_date_before_cents):,.0f}"
            )
            st.progress(pct / 100 if pct <= 100 else 1.0)
            st.markdown(f"Paid THIS event: **${from_cents(tier.paid_this_run_cents):,.0f}** ({pct}% of target satisfied overall)")

        if tier.per_stakeholder:
            rows = [(stakeholder_names.get(sid, "Unknown"), amt) for sid, amt in tier.per_stakeholder.items()]
            st.table({"Stakeholder": [r[0] for r in rows], "Amount": [f"${from_cents(r[1]):,.2f}" for r in rows]})
        st.divider()

if result.residual_cents > 0:
    st.info(f"Residual after all defined tiers: ${from_cents(result.residual_cents):,.2f} (add another tier to absorb it).")

st.markdown("### 🧾 Total per stakeholder this event")
totals = [(stakeholder_names.get(sid, "Unknown"), amt) for sid, amt in result.per_stakeholder_total.items() if amt > 0]
if totals:
    st.table({"Stakeholder": [t[0] for t in totals], "Total": [f"${from_cents(t[1]):,.2f}" for t in totals]})
else:
    st.caption("No distributions yet at this revenue level.")

st.caption(
    "This preview does **not** affect the ledger or entitlement balances — it's a what-if. "
    "Go to the Revenue page to actually process revenue and record it."
)
