import streamlit as st
from database.db import get_conn, generate_creator_id, today
from models.stakeholder import ROLES, ENTITLEMENT_TYPES
from engine.registry import create_stakeholder_with_initial_version, amend_agreement, list_agreement_versions
from money import to_cents, from_cents

st.set_page_config(page_title="Rights Registry", page_icon="🪪", layout="wide")
st.title("🪪 Team & Rights Registry")

active_id = st.session_state.get("active_project_id")
if not active_id:
    st.warning("Create or select a project on the Project Dashboard page first.")
    st.stop()

with get_conn() as conn:
    project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (active_id,)).fetchone())

st.caption(f"Editing registry for **{project['proxy_id']} — {project['title']}**")
st.info(
    "Entitlement type, pool, investment and premium here are **default contract terms** — "
    "a starting point when you assign this person to a tier on the Waterfall Builder page. "
    "The tier assignment is what actually controls the calculation."
)

ENTITLEMENT_LABELS = {
    "percentage": "Percentage of gross revenue",
    "fixed_recoupment": "Fixed recoupment (no premium)",
    "recoupment_plus_premium": "Recoupment + premium",
    "continuing_participation": "Continuing profit participation (points)",
}

with st.expander("➕ Add team member / rightsholder"):
    with st.form("add_stakeholder_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Name")
        role = c2.selectbox("Role", ROLES)
        pool = c3.selectbox("Default pool", ["investor", "creative"])

        entitlement_type = st.selectbox(
            "Default entitlement type", ENTITLEMENT_TYPES, format_func=lambda x: ENTITLEMENT_LABELS[x]
        )

        c4, c5 = st.columns(2)
        points = c4.number_input("Default points / % of gross", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        premium_pct = c5.number_input("Default premium (%)", min_value=0.0, step=1.0, value=0.0)
        investment_amount = st.number_input("Investment amount ($)", min_value=0.0, step=1000.0, value=0.0)

        submitted = st.form_submit_button("Add to registry")
        if submitted:
            if not name:
                st.error("Enter a name first.")
            else:
                creator_id = generate_creator_id()
                with get_conn() as conn:
                    create_stakeholder_with_initial_version(
                        conn, active_id, creator_id, name, role, points, entitlement_type,
                        to_cents(investment_amount), premium_pct, pool, "KYC_REQUIRED",
                    )
                st.success(f"Added {name} with Creator ID {creator_id}")
                st.rerun()

st.divider()

with get_conn() as conn:
    stakeholders = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM stakeholders WHERE project_id=? ORDER BY id", (active_id,)
        ).fetchall()
    ]

if not stakeholders:
    st.info("No stakeholders yet — add the writer, director, investors, etc. above.")
else:
    st.subheader("Registry")
    for s in stakeholders:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([2, 1.3, 1.3, 1.7, 1.5])
            c1.markdown(f"**{s['name']}**  \n`{s['creator_id']}`")
            c2.markdown(f"**Role**  \n{s['role']}")
            points_label = f"{s['points']}%" if s["entitlement_type"] == "percentage" else f"{s['points']} pts"
            c3.markdown(f"**Pool / Points**  \n{s['pool']} · {points_label}")
            c4.markdown(f"**Entitlement**  \n{ENTITLEMENT_LABELS.get(s['entitlement_type'], s['entitlement_type'])}")
            if s["investment_amount_cents"]:
                c5.markdown(
                    f"**Investment**  \n${from_cents(s['investment_amount_cents']):,.0f} @ {s['premium_pct']:.0f}% premium"
                )
            else:
                c5.markdown("**Investment**  \n—")

            with st.expander(f"📜 Agreement history — {s['name']}"):
                versions = list_agreement_versions(s["id"])
                for i, v in enumerate(versions):
                    label = "Agreement v1" if i == 0 else f"Amendment #{i}"
                    st.markdown(
                        f"**{label}** — Effective `{v['effective_date']}`  \n"
                        f"Points: {v['points']} · Pool: {v['pool']} · Entitlement: "
                        f"{ENTITLEMENT_LABELS.get(v['entitlement_type'], v['entitlement_type'])} · "
                        f"Investment: ${from_cents(v['investment_amount_cents']):,.0f} @ {v['premium_pct']:.0f}% premium  \n"
                        f"_{v['note'] or ''}_"
                    )
                    st.markdown("---")

                st.markdown("**Post a new amendment** (writes a full new version — nothing is overwritten)")
                with st.form(f"amend_form_{s['id']}", clear_on_submit=True):
                    ac1, ac2, ac3 = st.columns(3)
                    new_points = ac1.number_input(
                        "Points / % of gross", min_value=0.0, max_value=100.0,
                        value=float(s["points"]), step=0.5, key=f"amend_points_{s['id']}",
                    )
                    new_pool = ac2.selectbox(
                        "Pool", ["investor", "creative"],
                        index=["investor", "creative"].index(s["pool"]) if s["pool"] in ("investor", "creative") else 0,
                        key=f"amend_pool_{s['id']}",
                    )
                    new_entitlement = ac3.selectbox(
                        "Entitlement type", ENTITLEMENT_TYPES,
                        index=ENTITLEMENT_TYPES.index(s["entitlement_type"]) if s["entitlement_type"] in ENTITLEMENT_TYPES else 0,
                        format_func=lambda x: ENTITLEMENT_LABELS[x], key=f"amend_ent_{s['id']}",
                    )
                    ac4, ac5 = st.columns(2)
                    new_investment = ac4.number_input(
                        "Investment ($)", min_value=0.0, step=1000.0,
                        value=from_cents(s["investment_amount_cents"]), key=f"amend_inv_{s['id']}",
                    )
                    new_premium = ac5.number_input(
                        "Premium (%)", min_value=0.0, step=1.0,
                        value=float(s["premium_pct"]), key=f"amend_prem_{s['id']}",
                    )
                    effective = st.date_input("Effective date", key=f"amend_date_{s['id']}")
                    note = st.text_input("Note", placeholder="e.g. Amendment #2 — additional points for reshoot work", key=f"amend_note_{s['id']}")

                    if st.form_submit_button("Post amendment"):
                        amend_agreement(
                            s["id"], str(effective), note,
                            points=new_points, pool=new_pool, entitlement_type=new_entitlement,
                            investment_amount_cents=to_cents(new_investment), premium_pct=new_premium,
                        )
                        st.success("Amendment recorded — a new agreement version now applies going forward.")
                        st.rerun()
