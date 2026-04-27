-- Migration 001: add paper_test_filter JSONB column to wallet_strategy_analysis
-- Run once against the Neon Postgres database.
-- Safe to re-run: the IF NOT EXISTS guard prevents duplicate column errors.

ALTER TABLE wallet_strategy_analysis
    ADD COLUMN IF NOT EXISTS paper_test_filter JSONB;
