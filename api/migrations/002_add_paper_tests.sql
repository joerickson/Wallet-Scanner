-- Migration 002: add paper_tests and paper_trades tables
-- Run once against the Neon Postgres database.

CREATE TABLE IF NOT EXISTS paper_tests (
    id TEXT PRIMARY KEY,
    wallet_address TEXT NOT NULL,
    strategy_analysis_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    capital_allocated NUMERIC NOT NULL DEFAULT 10000,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC NOT NULL DEFAULT 0,
    last_evaluated_at TIMESTAMPTZ,
    filter_snapshot JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    paper_test_id TEXT NOT NULL REFERENCES paper_tests(id) ON DELETE CASCADE,
    polymarket_condition_id TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome_name TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price NUMERIC NOT NULL,
    entry_size_usd NUMERIC NOT NULL,
    entry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exit_price NUMERIC,
    exit_at TIMESTAMPTZ,
    exit_reason TEXT,
    realized_pnl NUMERIC,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_test ON paper_trades(paper_test_id);
CREATE INDEX IF NOT EXISTS idx_paper_tests_status ON paper_tests(status);
CREATE INDEX IF NOT EXISTS idx_paper_tests_user ON paper_tests(user_id);
