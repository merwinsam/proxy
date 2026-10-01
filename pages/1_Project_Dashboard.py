import streamlit as st
from database.db import get_conn, generate_proxy_id, today
from money import to_cents, from_cents

st.set_page_config(page_title="Project Dashboard", page_icon="🎬", layout="wide")
st.title("🎬 Project Dashboard")

with st.expander("➕ Create a new project"):
    with st.form("new_project_form"):
        c1, c2 = st.columns(2)
        title = c1.text_input("Project title", placeholder="e.g. MIDNIGHT HARBOR")
        project_type = c2.selectbox(
            "Project type", ["Feature Film", "Documentary", "Short Film", "TV Series", "Other"]
        )
        producer = c1.text_input("Producer", placeholder="e.g. Riverside Pictures")
        budget = c2.number_input("Budget ($)", min_value=0.0, step=1000.0, value=100000.0)
        submitted = st.form_submit_button("Create project")

        if submitted:
            if not title:
                st.error("Give the project a title first.")
            else:
                proxy_id = generate_proxy_id()
                with get_conn() as conn:
                    conn.execute(
                        """INSERT INTO projects (proxy_id, title, project_type, producer, budget_cents,
                                                  revenue_received_cents, distributable_cash_cents, created_on)
                           VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
                        (proxy_id, title, project_type, producer, to_cents(budget), today()),
                    )
                st.success(f"Created {proxy_id} — {title}")
                st.rerun()

st.divider()

with get_conn() as conn:
    projects = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()]

if not projects:
    st.info("No projects yet — create one above.")
else:
    for p in projects:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
            c1.markdown(f"**{p['proxy_id']}**  \n{p['title']}")
            c2.markdown(f"**Type**  \n{p['project_type'] or '—'}")
            c3.markdown(f"**Producer**  \n{p['producer'] or '—'}")
            c4.markdown(f"**Budget**  \n${from_cents(p['budget_cents']):,.0f}")
            c5.markdown(f"**Revenue received**  \n${from_cents(p['revenue_received_cents']):,.0f}")

            if p["id"] == st.session_state.get("active_project_id"):
                c5.caption("✅ Active project")
            else:
                if c5.button("Set active", key=f"activate_{p['id']}"):
                    st.session_state.active_project_id = p["id"]
                    st.rerun()
