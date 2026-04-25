from __future__ import annotations

from datetime import datetime

import pytest

from analysis.red_flags import (
    check_market_concentration,
    check_recency_cliff,
    check_single_bet_dominance,
    check_survivorship,
    get_red_flags,
)
from data.schema import WalletMetrics


def _metrics(
    address: str = "0xtest",
    trade_count: int = 50,
    total_pnl: float | None = 15000.0,
    total_volume: float | None = 80_000.0,
    market_count: int = 10,
    top_market_concentration: float | None = 0.15,
    realized_position_count: int = 35,
    unresolved_position_count: int = 15,
    pct_pnl_from_top_3: float | None = 0.30,
) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        total_pnl=total_pnl,
        total_volume=total_volume,
        market_count=market_count,
        top_market_concentration=top_market_concentration,
        realized_position_count=realized_position_count,
        unresolved_position_count=unresolved_position_count,
        pct_pnl_from_top_3_positions=pct_pnl_from_top_3,
        computed_at=datetime.utcnow(),
    )


# ── single_bet_dominance ──────────────────────────────────────────────────────

class TestSingleBetDominance:
    def test_flags_when_top3_pnl_above_70pct(self):
        m = _metrics(pct_pnl_from_top_3=0.85)
        assert check_single_bet_dominance(m) is True

    def test_clears_when_top3_pnl_below_70pct(self):
        m = _metrics(pct_pnl_from_top_3=0.40)
        assert check_single_bet_dominance(m) is False

    def test_clears_when_pct_is_none(self):
        m = _metrics(pct_pnl_from_top_3=None)
        assert check_single_bet_dominance(m) is False

    def test_boundary_at_exactly_70pct(self):
        m = _metrics(pct_pnl_from_top_3=0.70)
        # 0.70 is NOT > 0.70 — should not flag
        assert check_single_bet_dominance(m) is False


# ── market_concentration ──────────────────────────────────────────────────────

class TestMarketConcentration:
    def test_flags_high_concentration_many_positions(self):
        m = _metrics(top_market_concentration=0.85, trade_count=25, market_count=3)
        assert check_market_concentration(m) is True

    def test_clears_when_concentration_below_threshold(self):
        m = _metrics(top_market_concentration=0.40)
        assert check_market_concentration(m) is False

    def test_clears_when_below_minimum_positions(self):
        m = _metrics(top_market_concentration=0.90, trade_count=5)
        assert check_market_concentration(m) is False

    def test_clears_when_concentration_is_none(self):
        m = _metrics(top_market_concentration=None)
        assert check_market_concentration(m) is False


# ── survivorship ──────────────────────────────────────────────────────────────

class TestSurvivorship:
    def test_flags_when_unresolved_far_exceeds_resolved(self):
        m = _metrics(realized_position_count=10, unresolved_position_count=35)
        assert check_survivorship(m) is True

    def test_clears_when_unresolved_within_ratio(self):
        m = _metrics(realized_position_count=20, unresolved_position_count=40)
        # 40 < 20 * 3 = 60 — should not flag
        assert check_survivorship(m) is False

    def test_clears_when_no_resolved_positions(self):
        m = _metrics(realized_position_count=0, unresolved_position_count=50)
        assert check_survivorship(m) is False

    def test_boundary_exactly_at_ratio(self):
        m = _metrics(realized_position_count=10, unresolved_position_count=30)
        # 30 is NOT > 10 * 3 = 30 — should not flag
        assert check_survivorship(m) is False


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
        assert check_recency_cliff(recent_win_rate=0.30, overall_win_rate=0.55) is False


# ── get_red_flags (aggregate) ─────────────────────────────────────────────────

class TestGetRedFlags:
    def test_clean_wallet_has_no_flags(self):
        m = _metrics()
        flags = get_red_flags(m)
        assert flags == []

    def test_detects_single_bet_dominance(self, concentrated_pnl_positions, wallet_address):
        from scanner.metrics import compute_metrics

        m = compute_metrics(concentrated_pnl_positions, 50000.0, 100000.0, None)
        assert m is not None
        flags = get_red_flags(m)
        assert "single_bet_dominance" in flags

    def test_detects_market_concentration(self, single_market_positions, wallet_address):
        from scanner.metrics import compute_metrics

        m = compute_metrics(single_market_positions, 10000.0, 50000.0, None)
        assert m is not None
        flags = get_red_flags(m)
        assert "market_concentration" in flags

    def test_detects_survivorship(self):
        m = _metrics(realized_position_count=5, unresolved_position_count=20)
        flags = get_red_flags(m)
        assert "survivorship" in flags

    def test_returns_list_not_set(self):
        m = _metrics()
        assert isinstance(get_red_flags(m), list)

    def test_no_false_positive_on_normal_wallet(self, basic_metrics):
        flags = get_red_flags(basic_metrics)
        assert len(flags) == 0, f"Unexpected flags: {flags}"
