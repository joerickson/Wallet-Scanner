from __future__ import annotations

from datetime import datetime

import pytest

from data.schema import Position
from scanner.metrics import (
    apply_hard_filters,
    compute_metrics,
    parse_positions,
)


# ── parse_positions ───────────────────────────────────────────────────────────

class TestParsePositions:
    def test_parses_standard_record(self):
        raw = [
            {
                "conditionId": "0xcondition1",
                "asset": "0xasset1",
                "title": "Will X happen?",
                "slug": "will-x-happen",
                "outcome": "Yes",
                "avgPrice": 0.65,
                "size": 100.0,
                "initialValue": 100.0,
                "currentValue": 150.0,
                "cashPnl": 50.0,
                "percentPnl": 0.50,
                "totalBought": 100.0,
                "realizedPnl": 50.0,
                "percentRealizedPnl": 0.50,
                "curPrice": 1.0,
                "redeemable": True,
                "endDate": "2024-06-01T00:00:00Z",
            }
        ]
        positions = parse_positions("0xwallet", raw)
        assert len(positions) == 1
        p = positions[0]
        assert p.wallet_address == "0xwallet"
        assert p.condition_id == "0xcondition1"
        assert p.outcome == "Yes"
        assert p.size == pytest.approx(100.0)
        assert p.avg_price == pytest.approx(0.65)
        assert p.cash_pnl == pytest.approx(50.0)
        assert p.redeemable is True

    def test_skips_records_without_condition_id(self):
        raw = [{"asset": "0xasset", "outcome": "Yes", "size": 100}]
        positions = parse_positions("0xwallet", raw)
        assert len(positions) == 0

    def test_handles_empty_list(self):
        assert parse_positions("0xwallet", []) == []

    def test_handles_malformed_records_gracefully(self):
        raw = [
            {"conditionId": "0xcond1", "size": "not_a_float"},
            {"conditionId": "0xcond2", "size": 100.0},
        ]
        positions = parse_positions("0xwallet", raw)
        assert any(p.condition_id == "0xcond2" for p in positions)

    def test_redeemable_defaults_to_false(self):
        raw = [{"conditionId": "0xcond1"}]
        positions = parse_positions("0xwallet", raw)
        assert len(positions) == 1
        assert positions[0].redeemable is False

    def test_parses_snake_case_fields(self):
        raw = [{"condition_id": "0xcond1", "avg_price": 0.5, "cash_pnl": 10.0}]
        positions = parse_positions("0xwallet", raw)
        assert len(positions) == 1
        assert positions[0].condition_id == "0xcond1"
        assert positions[0].avg_price == pytest.approx(0.5)
        assert positions[0].cash_pnl == pytest.approx(10.0)


# ── compute_metrics ───────────────────────────────────────────────────────────

class TestComputeMetrics:
    def test_returns_none_for_empty_positions(self):
        assert compute_metrics([], None, None, None) is None

    def test_uses_leaderboard_pnl_and_vol(self, resolved_positions):
        m = compute_metrics(resolved_positions, leaderboard_pnl=12345.0, leaderboard_vol=98765.0, portfolio_value=None)
        assert m is not None
        assert m.total_pnl == pytest.approx(12345.0)
        assert m.total_volume == pytest.approx(98765.0)

    def test_trade_count_equals_position_count(self, resolved_positions):
        m = compute_metrics(resolved_positions, None, None, None)
        assert m is not None
        assert m.trade_count == len(resolved_positions)

    def test_realized_count_from_redeemable(self, mixed_positions):
        m = compute_metrics(mixed_positions, None, None, None)
        assert m is not None
        resolved = sum(1 for p in mixed_positions if p.redeemable)
        unresolved = sum(1 for p in mixed_positions if not p.redeemable)
        assert m.realized_position_count == resolved
        assert m.unresolved_position_count == unresolved

    def test_market_count_counts_unique_condition_ids(self, resolved_positions):
        m = compute_metrics(resolved_positions, None, None, None)
        assert m is not None
        assert m.market_count == 10  # fixture has 10 distinct markets

    def test_top_market_concentration_single_market(self, single_market_positions):
        m = compute_metrics(single_market_positions, None, None, None)
        assert m is not None
        assert m.top_market_concentration == pytest.approx(1.0)

    def test_portfolio_value_stored(self, resolved_positions):
        m = compute_metrics(resolved_positions, None, None, portfolio_value=42000.0)
        assert m is not None
        assert m.portfolio_value == pytest.approx(42000.0)

    def test_pct_pnl_top3_computed(self, concentrated_pnl_positions):
        m = compute_metrics(concentrated_pnl_positions, None, None, None)
        assert m is not None
        assert m.pct_pnl_from_top_3_positions is not None
        # The big winner dominates — fraction should be high
        assert m.pct_pnl_from_top_3_positions > 0.7

    def test_pct_pnl_top3_none_when_no_cash_pnl(self, wallet_address):
        positions = [
            Position(
                wallet_address=wallet_address,
                condition_id=f"m{i}",
                cash_pnl=None,
            )
            for i in range(5)
        ]
        m = compute_metrics(positions, None, None, None)
        assert m is not None
        assert m.pct_pnl_from_top_3_positions is None

    def test_avg_and_max_position_size(self, resolved_positions):
        m = compute_metrics(resolved_positions, None, None, None)
        assert m is not None
        assert m.avg_position_size is not None
        assert m.max_position_size_usd is not None


# ── apply_hard_filters ────────────────────────────────────────────────────────

class TestApplyHardFilters:
    def test_passes_qualifying_wallet(self, basic_metrics):
        result = apply_hard_filters(
            [basic_metrics],
            min_trades=30,
            min_pnl=5000.0,
            min_volume=5000.0,
            min_realized_positions=10,
        )
        assert len(result) == 1

    def test_rejects_insufficient_positions(self, basic_metrics):
        basic_metrics.trade_count = 10
        result = apply_hard_filters([basic_metrics], min_trades=30)
        assert len(result) == 0

    def test_rejects_low_pnl(self, basic_metrics):
        basic_metrics.total_pnl = 100.0
        result = apply_hard_filters([basic_metrics], min_pnl=5000.0)
        assert len(result) == 0

    def test_rejects_none_pnl(self, basic_metrics):
        basic_metrics.total_pnl = None
        result = apply_hard_filters([basic_metrics])
        assert len(result) == 0

    def test_rejects_low_volume(self, basic_metrics):
        basic_metrics.total_volume = 1000.0
        result = apply_hard_filters([basic_metrics], min_volume=5000.0)
        assert len(result) == 0

    def test_rejects_too_few_realized_positions(self, basic_metrics):
        basic_metrics.realized_position_count = 5
        result = apply_hard_filters([basic_metrics], min_realized_positions=10)
        assert len(result) == 0

    def test_empty_list_returns_empty(self):
        assert apply_hard_filters([]) == []

    def test_filters_multiple_wallets(self, wallet_address, basic_metrics):
        from data.schema import WalletMetrics

        poor = WalletMetrics(
            wallet_address="0xpoor",
            trade_count=10,
            total_pnl=50.0,
            total_volume=1000.0,
            realized_position_count=3,
            computed_at=datetime.utcnow(),
        )
        result = apply_hard_filters([basic_metrics, poor])
        assert len(result) == 1
        assert result[0].wallet_address == wallet_address
