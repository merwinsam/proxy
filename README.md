# Proxy — Prototype 1 (v3)

A working Streamlit prototype of Proxy: a rights, revenue and waterfall
distribution engine for film/media projects. Built to demonstrate the core
intellectual property — the waterfall calculation engine — without moving
any real money.

**v2** made the waterfall *stateful* (it no longer forgets how much of a
recoupment or expense-cap target has already been paid) and fixed ledger
double-counting and payout batching. See [CHANGES.md](CHANGES.md).

**v3** makes the contract explicit: which specific stakeholders belong to
which tier (not "every investor globally"), a full versioned agreement
history per stakeholder (not just points), gross-percentage contracts that
are rejected outright rather than silently repaired if they overflow 100%,
and a fixed investor/creative profit-split percentage that can't silently
disagree with itself. See [CHANGES_V3.md](CHANGES_V3.md).

## Run it

```bash
cd proxy
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

A demo project, **THE LAST SIGNAL**, is seeded automatically on first run,
using the exact numbers from the spec:

- $150,000 incoming revenue
- $20,000 expense cap (Tier 1)
- $80,000 investor capital at a 20% premium → $96,000 recoupment target (Tier 2)
- Remaining $34,000 split 50/50 between the investor pool and creative pool (Tier 3)

Approve the seeded pending expenses on the **Expenses** page, then process
$150,000 of revenue on the **Revenue** page, and you'll see the exact
$20,000 / $96,000 / $34,000 cascade land in the ledger and payout list.

## What's here (Prototype 1 scope)

- **Project Dashboard** — create projects, get a fake `PROXY-000001`-style ID
- **Rights Registry** — add stakeholders with a fake Creator ID, points, and
  an entitlement type (percentage / fixed recoupment / recoupment + premium /
  continuing participation)
- **Waterfall Builder** — define tiers (expense cap, recoupment, profit
  split) and preview the cascade live against a hypothetical revenue amount
- **Expenses** — a staging queue; card transactions sit as `PENDING_REVIEW`
  and never touch the waterfall until approved, disallowed, or split
- **Revenue Simulator** — pick a source/territory, enter an amount, and run
  the real waterfall engine against it (no Stripe involved)
- **Audit Ledger** — an append-only ledger of every revenue, expense,
  tier-completion and distribution event, plus a correction tool that posts
  offsetting entries rather than editing history
- **Payouts** — simulated payout records (`READY` / `KYC_REQUIRED` / `PAID`)
  standing in for where Stripe Connect transfers would eventually plug in

## What's deliberately left out

Real Stripe Connect payouts, actual card issuance, KYC verification, ACH
transfers, tax documents, and live bank integrations. These are represented
as simulated states (`KYC_REQUIRED`, `SIMULATE PAYOUT`) so the distribution
logic can be proven out before taking on that regulatory and security
burden.

## Architecture

```
proxy/
├── app.py                  # entry point, DB init, demo seed, project selector
├── money.py                 # integer-cents helpers + largest-remainder pro-rata split
├── pages/
│   ├── 1_Project_Dashboard.py
│   ├── 2_Rights_Registry.py    # + append-only points amendment history
│   ├── 3_Waterfall_Builder.py  # + live entitlement-balance display
│   ├── 4_Expenses.py
│   ├── 5_Revenue.py            # now a thin wrapper around engine/revenue.py
│   └── 6_Audit_Ledger.py       # + payout batches grouped by revenue event
├── engine/
│   ├── waterfall.py          # the core IP: contract-driven, stateful cascade
│   ├── tier_participants.py  # the contract: who belongs in which tier, on what terms
│   ├── tier_progress.py      # persistent "target − paid_to_date" balances
│   ├── revenue.py            # single transaction that owns a whole revenue event
│   ├── expenses.py           # staging queue: approve / disallow / split
│   ├── ledger.py             # reads + manual corrections only
│   ├── payouts.py            # payout batches, one per (stakeholder, revenue event)
│   ├── registry.py           # full agreement-version history (not just points)
│   └── provenance.py         # "why did X receive $Y?" audit reconstruction
├── models/                  # plain dataclasses mirroring the DB rows
│   ├── project.py
│   ├── stakeholder.py
│   └── transaction.py
└── database/
    └── db.py                 # SQLite schema (integer cents) + connection helper
```

The layering matters more than the UI: `Streamlit UI → Project/Contract Data
→ Waterfall Engine → Ledger → Simulated Payouts`. When this eventually moves
off Streamlit onto a real frontend, only the `pages/` layer changes — the
`engine/` and `database/` layers don't need to be rewritten. Swapping SQLite
for Postgres later only touches `database/db.py`.
