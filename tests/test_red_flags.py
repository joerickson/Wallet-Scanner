from __future__ import annotations

from datetime import datetime

import pytest

from analysis.red_flags import (
    check_insider_timing,
    check_market_concentration,
    check_recency_cliff,
    check_single_bet_dominance,
    check_survivorship_bias,
    check_volume_size_mismatch,
    get_red_flags,
)
from data.schema import WalletMetrics


def _metrics(
    address: str = "0xtest",
    trade_count: int = 120,
    win_rate: float | None = 0.65,
    total_pnl: float | None = 3000.0,
    total_volume: float | None = 20_000.0,
    market_count: int = 10,
    top_market_concentration: float | None = 0.15,
) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_volume=total_volume,
        market_count=market_count,
        top_market_concentration=top_market_concentration,
        computed_at=datetime.utcnow(),
    )


# ── single_bet_dominance ──────────────────────────────────────────────────────

class TestSingleBetDominance:
    def test_flags_when_concentration_above_50pct(self):
        m = _metrics(top_market_concentration=0.75)
        assert check_single_bet_dominance(m) is True

    def test_clears_when_concentration_below_50pct(self):
        m = _metrics(top_market_concentration=0.30)
        assert check_single_bet_dominance(m) is False

    def test_clears_when_concentration_is_none(self):
        m = _metrics(top_market_concentration=None)
        assert check_single_bet_dominance(m) is False

    def test_boundary_at_exactly_50pct(self):
        m = _metrics(top_market_concentration=0.50)
        # 0.50 is NOT > 0.50 — should not flag
        assert check_single_bet_dominance(m) is False


# ── market_concentration ──────────────────────────────────────────────────────

class TestMarketConcentration:
    def test_flags_few_markets_many_trades(self):
        m = _metrics(market_count=2, trade_count=50)
        assert check_market_concentration(m) is True

    def test_clears_when_many_markets(self):
        m = _metrics(market_count=20)
        assert check_market_concentration(m) is False

    def test_clears_when_below_minimum_trades(self):
        m = _metrics(market_count=2, trade_count=10)
        assert check_market_concentration(m) is False


# ── survivorship_bias ─────────────────────────────────────────────────────────

class TestSurvivorshipBias:
    def test_flags_high_win_rate_low_trades(self):
        m = _metrics(win_rate=0.95, trade_count=150)
        assert check_survivorship_bias(m) is True

    def test_clears_when_enough_trades(self):
        m = _metrics(win_rate=0.95, trade_count=300)
        assert check_survivorship_bias(m) is False

    def test_clears_when_win_rate_below_threshold(self):
        m = _metrics(win_rate=0.75, trade_count=100)
        assert check_survivorship_bias(m) is False

    def test_clears_when_win_rate_is_none(self):
        m = _metrics(win_rate=None, trade_count=100)
        assert check_survivorship_bias(m) is False


# ── volume_size_mismatch ──────────────────────────────────────────────────────

class TestVolumeSizeMismatch:
    def test_flags_when_pnl_exceeds_300pct_of_volume(self):
        m = _metrics(total_pnl=70_000.0, total_volume=20_000.0)  # 350% ROI
        assert check_volume_size_mismatch(m) is True

    def test_clears_reasonable_roi(self):
        m = _metrics(total_pnl=3000.0, total_volume=20_000.0)  # 15% ROI
        assert check_volume_size_mismatch(m) is False

    def test_clears_when_pnl_is_none(self):
        m = _metrics(total_pnl=None)
        assert check_volume_size_mismatch(m) is False

    def test_clears_when_volume_is_zero(self):
        m = _metrics(total_volume=0.0)
        assert check_volume_size_mismatch(m) is False


# ── recency_cliff ─────────────────────────────────────────────────────────────

class TestRecencyCliff:
    def test_flags_significant_performance_drop(self):
        assert check_recency_cliff(recent_win_rate=0.35, overall_win_rate=0.70) is True

    def test_clears_modest_drop(self):
        assert check_recency_cliff(recent_win_rate=0.55, overall_win_rate=0.65) is False

    def test_clears_when_either_is_none(self):
        assert check_recency_cliff(None, 0.70) is False
        assert check_recency_cliff(0.40, None) is False

    def test_clears_when_overall_below_60pct(self):
        # Don't flag wallets that barely passed the filter themselves
        assert check_recency_cliff(recent_win_rate=0.30, overall_win_rate=0.55) is False


# ── insider_timing ────────────────────────────────────────────────────────────

class TestInsiderTiming:
    def test_flags_early_entries(self):
        assert check_insider_timing(avg_entry_time_after_open_hours=0.3) is True

    def test_clears_normal_entry_time(self):
        assert check_insider_timing(avg_entry_time_after_open_hours=6.0) is False

    def test_clears_when_none(self):
        assert check_insider_timing(None) is False


# ── get_red_flags (aggregate) ─────────────────────────────────────────────────

class TestGetRedFlags:
    def test_clean_wallet_has_no_flags(self):
        m = _metrics()
        flags = get_red_flags(m)
        assert flags == []

    def test_detects_single_bet_dominance(self, single_market_trades, wallet_address):
        from scanner.metrics import compute_metrics

        m = compute_metrics(single_market_trades)
        assert m is not None
        flags = get_red_flags(m)
        assert "single_bet_dominance" in flags

    def test_detects_survivorship_bias(self, high_win_rate_sparse, wallet_address):
        from scanner.metrics import compute_metrics

        m = compute_metrics(high_win_rate_sparse)
        assert m is not None
        flags = get_red_flags(m)
        assert "survivorship_bias" in flags

    def test_detects_volume_size_mismatch(self):
        m = _metrics(total_pnl=80_000.0, total_volume=10_000.0)
        flags = get_red_flags(m)
        assert "volume_size_mismatch" in flags

    def test_returns_list_not_set(self):
        m = _metrics()
        assert isinstance(get_red_flags(m), list)

    def test_no_false_positive_on_normal_wallet(self, basic_metrics):
        flags = get_red_flags(basic_metrics)
        # A reasonable wallet should trigger no flags
        assert len(flags) == 0, f"Unexpected flags: {flags}"
