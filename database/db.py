"""
Proxy database layer.

SQLite is the source of truth for Prototype 1. All money columns are
INTEGER cents (see money.py) — never REAL — so the engine's recoupment
math and ledger reconciliation can't drift from float rounding.

Schema notes vs. earlier prototypes:
  - waterfall_tiers gained a 'gross_percentage' tier_type for an off-the-top
    cut of gross revenue.
  - tier_progress is the persistent "contractual target minus paid-to-date"
    balance per tier (and per stakeholder, for recoupment tiers) that makes
    the waterfall stateful across multiple revenue events.
  - revenue_events: every processed revenue event is its own batch, and
    distributions/payouts/ledger entries reference it, so paying out one
    batch can never bleed into another.
  - tier_participants is new (v3): the CONTRACT now explicitly says who
    belongs in which tier and on what terms, instead of the engine
    inferring membership from a stakeholder's global entitlement_type/pool.
    A stakeholder not assigned to a tier simply isn't touched by it — this
    is what lets you model "Tier 2 recoups Alice and Bob; Tier 3 recoups
    Charlie" instead of every recoupment-typed investor landing in every
    recoupment tier.
  - agreement_versions is new (v3): replaces the points-only amendment log
    with a full contract-term snapshot (points, entitlement_type,
    investment, premium, pool) every time any of those change, so a past
    distribution can point at exactly which version of the agreement was
    in effect when it was calculated. Resolution is BY EFFECTIVE DATE, not
    insertion order (v4) — a future-dated amendment never leaks into a
    distribution processed before it takes effect.
  - tier_progress_adjustments and the distributions snapshot columns are
    new (v4): a contract amendment now adjusts (not silently replaces) a
    stakeholder's recoupment target with a logged delta, and each
    distribution freezes the exact rule/rate/points that produced it, so
    "why did this payout happen" can never change after the fact even if
    the contract is edited later.
"""

import sqlite3
import random
import string
from contextlib import contextmanager
from datetime import date

DB_PATH = "database/proxy.db"


def _new_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = _new_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                project_type TEXT,
                producer TEXT,
                budget_cents INTEGER DEFAULT 0,
                revenue_received_cents INTEGER DEFAULT 0,
                distributable_cash_cents INTEGER DEFAULT 0,
                created_on TEXT
            );

            CREATE TABLE IF NOT EXISTS stakeholders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                creator_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                role TEXT,
                points REAL DEFAULT 0,
                entitlement_type TEXT,          -- percentage | fixed_recoupment | recoupment_plus_premium | continuing_participation
                investment_amount_cents INTEGER DEFAULT 0,   -- canonical principal; used directly by recoupment math
                premium_pct REAL DEFAULT 0,                  -- default premium; tier_participants may override per tier
                pool TEXT,                      -- default pool; tier_participants may override per tier
                kyc_status TEXT DEFAULT 'KYC_REQUIRED'
            );

            -- Full contract-term snapshot every time points, entitlement_type,
            -- investment, premium, or pool changes for a stakeholder — not
            -- just points. distributions.agreement_version_id points at
            -- whichever version was current when that distribution was
            -- calculated, so "why did Anna get $X" can cite the exact terms.
            CREATE TABLE IF NOT EXISTS agreement_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stakeholder_id INTEGER NOT NULL REFERENCES stakeholders(id),
                effective_date TEXT,
                points REAL,
                entitlement_type TEXT,
                investment_amount_cents INTEGER,
                premium_pct REAL,
                pool TEXT,
                note TEXT,
                created_on TEXT
            );

            -- The contract, explicit: which stakeholders belong to which
            -- tier, and on what terms. A stakeholder not listed here for a
            -- given tier is simply not touched by that tier, regardless of
            -- their global entitlement_type/pool defaults above.
            --   rule_type: recoupment_fixed | recoupment_premium | gross_percentage | profit_points
            --   pool:      profit_points only — 'investor' | 'creative'
            --   rate_pct:  recoupment_premium's premium %, or gross_percentage's cut of gross
            --   points:    profit_points' weight within its pool
            CREATE TABLE IF NOT EXISTS tier_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier_id INTEGER NOT NULL REFERENCES waterfall_tiers(id),
                stakeholder_id INTEGER NOT NULL REFERENCES stakeholders(id),
                rule_type TEXT NOT NULL,
                pool TEXT,
                rate_pct REAL,
                points REAL,
                cap_cents INTEGER,
                UNIQUE(tier_id, stakeholder_id)
            );

            CREATE TABLE IF NOT EXISTS waterfall_tiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                tier_number INTEGER NOT NULL,
                name TEXT NOT NULL,
                tier_type TEXT NOT NULL,        -- expense_cap | recoupment | gross_percentage | profit_split
                cap_amount_cents INTEGER DEFAULT 0,   -- expense_cap only
                investor_pool_pct REAL DEFAULT 50,    -- profit_split only; the ONE control the UI exposes
                creative_pool_pct REAL DEFAULT 50,    -- profit_split only; always kept = 100 - investor_pool_pct
                UNIQUE(project_id, tier_number)
            );

            -- Persistent "contractual target minus paid-to-date" balance.
            -- stakeholder_id IS NULL  -> tier-level balance (expense_cap).
            -- stakeholder_id NOT NULL -> per-stakeholder balance (recoupment).
            -- No row at all for a tier/stakeholder combo means "uncapped":
            -- gross_percentage and profit_split never accumulate a balance,
            -- they act on each revenue event fresh.
            CREATE TABLE IF NOT EXISTS tier_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                tier_id INTEGER NOT NULL REFERENCES waterfall_tiers(id),
                stakeholder_id INTEGER REFERENCES stakeholders(id),
                contractual_target_cents INTEGER,
                paid_to_date_cents INTEGER DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_progress_tier_level
                ON tier_progress(tier_id) WHERE stakeholder_id IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_progress_stakeholder_level
                ON tier_progress(tier_id, stakeholder_id) WHERE stakeholder_id IS NOT NULL;

            -- A contract amendment that changes a stakeholder's investment or
            -- a tier participant's rate doesn't silently rewrite the target —
            -- it logs the delta here and adjusts contractual_target_cents by
            -- that delta, so paid_to_date is preserved and the change stays
            -- auditable ("Original target $60,000, Amendment +$5,000, Revised
            -- target $65,000").
            CREATE TABLE IF NOT EXISTS tier_progress_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier_progress_id INTEGER NOT NULL REFERENCES tier_progress(id),
                effective_date TEXT,
                old_target_cents INTEGER,
                new_target_cents INTEGER,
                delta_cents INTEGER,
                note TEXT,
                created_on TEXT
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                vendor TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                description TEXT,
                card_holder TEXT,
                card_last4 TEXT,
                status TEXT DEFAULT 'PENDING_REVIEW',  -- PENDING_REVIEW | ALLOCATED | DISALLOWED
                created_on TEXT
            );

            -- One row per processed revenue event. Every ledger entry,
            -- distribution and payout stemming from that event carries its
            -- id, so a single $150,000 payment can never fan out into two
            -- independent, un-linked $150,000 records.
            CREATE TABLE IF NOT EXISTS revenue_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                source TEXT,
                territory TEXT,
                amount_cents INTEGER NOT NULL,
                is_sweep INTEGER DEFAULT 0,   -- 1 = reprocessing held distributable cash, not new revenue
                entry_date TEXT
            );

            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                revenue_event_id INTEGER REFERENCES revenue_events(id),
                entry_date TEXT,
                entry_type TEXT,       -- revenue | expense_payment | tier_info | distribution | correction
                description TEXT,
                amount_cents INTEGER NOT NULL,  -- signed: + inflow, - outflow. tier_info is always 0.
                related_id INTEGER
            );

            -- rule_type/pool/rate_pct/points/cap_cents are an IMMUTABLE
            -- SNAPSHOT of the tier_participants row that produced this
            -- distribution, captured at calculation time. tier_participants
            -- itself is mutable current-state (a producer can edit rates
            -- later) — without this snapshot, "why did this payout happen"
            -- would silently change if the contract is edited afterward.
            CREATE TABLE IF NOT EXISTS distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                revenue_event_id INTEGER NOT NULL REFERENCES revenue_events(id),
                stakeholder_id INTEGER NOT NULL REFERENCES stakeholders(id),
                tier_id INTEGER REFERENCES waterfall_tiers(id),
                agreement_version_id INTEGER REFERENCES agreement_versions(id),
                rule_type TEXT,
                pool TEXT,
                rate_pct REAL,
                points REAL,
                cap_cents INTEGER,
                amount_cents INTEGER NOT NULL,
                entry_date TEXT
            );

            -- One payout batch per (stakeholder, revenue_event) — never an
            -- all-time running sum. Paying out one batch can't inflate or
            -- get confused with another.
            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stakeholder_id INTEGER NOT NULL REFERENCES stakeholders(id),
                revenue_event_id INTEGER NOT NULL REFERENCES revenue_events(id),
                amount_cents INTEGER NOT NULL,
                status TEXT DEFAULT 'READY',  -- READY | KYC_REQUIRED | PAID (simulated)
                entry_date TEXT,
                UNIQUE(stakeholder_id, revenue_event_id)
            );
            """
        )
        _migrate_columns(conn)


# Columns added to already-existing tables across schema versions.
# CREATE TABLE IF NOT EXISTS above only creates a table the FIRST time it's
# seen — it never retroactively adds a column to a table that already
# exists on disk from an earlier version. Without this, a database file
# created before a given version keeps failing with "no such column" even
# though the code has moved on. This runs on every init_db() call and is a
# no-op once a column is present.
_SCHEMA_MIGRATIONS = [
    # (table, column, type-and-default-for-ALTER-TABLE)
    ("distributions", "agreement_version_id", "INTEGER"),
    ("distributions", "rule_type", "TEXT"),
    ("distributions", "pool", "TEXT"),
    ("distributions", "rate_pct", "REAL"),
    ("distributions", "points", "REAL"),
    ("distributions", "cap_cents", "INTEGER"),
    ("revenue_events", "is_sweep", "INTEGER DEFAULT 0"),
]


def _migrate_columns(conn):
    for table, column, col_type in _SCHEMA_MIGRATIONS:
        existing = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        if not existing:
            continue  # table doesn't exist yet on this connection's schema view — CREATE TABLE above handles it
        existing_cols = {row["name"] for row in existing}
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def generate_proxy_id():
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
    return f"PROXY-{count + 1:06d}"


def generate_creator_id():
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"CR-{suffix}"


def today():
    return date.today().isoformat()
