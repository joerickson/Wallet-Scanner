from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.schema import Trade
from scanner.metrics import (
    apply_hard_filters,
    compute_metrics,
    parse_trades,
)


# ── parse_trades ──────────────────────────────────────────────────────────────

class TestParseTrades:
    def test_parses_standard_record(self):
        raw = [
            {
                "type": "TRADE",
                "market": "0xmarket1",
                "side": "BUY",
                "outcome": "Yes",
                "usdcSize": 100.0,
                "price": 0.65,
                "pnl": 53.85,
                "resolved": True,
                "resolutionPrice": 1.0,
                "timestamp": "2024-01-01T00:00:00Z",
                "title": "Test market",
            }
        ]
        trades = parse_trades("0xwallet", raw)
        assert len(trades) == 1
        t = trades[0]
        assert t.wallet_address == "0xwallet"
        assert t.market_id == "0xmarket1"
        assert t.side == "BUY"
        assert t.size == 100.0
        assert t.price == 0.65
        assert t.pnl == pytest.approx(53.85)
        assert t.is_resolved is True

    def test_skips_records_without_timestamp(self):
        raw = [{"type": "TRADE", "market": "0xm", "side": "BUY", "usdcSize": 100}]
        trades = parse_trades("0xwallet", raw)
        assert len(trades) == 0

    def test_skips_records_without_market(self):
        raw = [{"type": "TRADE", "side": "BUY", "usdcSize": 100, "timestamp": "2024-01-01T00:00:00Z"}]
        trades = parse_trades("0xwallet", raw)
        assert len(trades) == 0

    def test_skips_non_trade_types(self):
        raw = [
            {"type": "MERGE", "market": "0xm", "usdcSize": 100, "timestamp": "2024-01-01T00:00:00Z"},
            {"type": "SPLIT", "market": "0xm", "usdcSize": 100, "timestamp": "2024-01-01T00:00:00Z"},
        ]
        trades = parse_trades("0xwallet", raw)
        assert len(trades) == 0

    def test_parses_unix_timestamp(self):
        raw = [{"type": "TRADE", "market": "0xm", "side": "BUY", "usdcSize": 50, "timestamp": 1704067200}]
        trades = parse_trades("0xwallet", raw)
        assert len(trades) == 1
        assert trades[0].timestamp == datetime(2024, 1, 1, 0, 0, 0)

    def test_handles_empty_list(self):
        assert parse_trades("0xwallet", []) == []

    def test_handles_malformed_records_gracefully(self):
        raw = [
            {"type": "TRADE", "market": "0xm", "side": "BUY", "price": "not_a_float", "usdcSize": 100, "timestamp": 1704067200},
            {"type": "TRADE", "market": "0xm2", "side": "BUY", "price": 0.5, "usdcSize": 100, "timestamp": 1704067200},
        ]
        # Should not crash; at least the valid record is parsed
        trades = parse_trades("0xwallet", raw)
        assert any(t.market_id == "0xm2" for t in trades)


# ── compute_metrics ───────────────────────────────────────────────────────────

class TestComputeMetrics:
    def test_returns_none_for_empty_trades(self):
        assert compute_metrics([]) is None

    def test_win_rate_computed_from_completed_trades(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        assert m.win_rate is not None
        # 60 wins / 120 trades with pnl — ~0.5 win rate in fixture (even/odd pattern)
        assert 0.45 <= m.win_rate <= 0.75

    def test_sharpe_is_none_below_threshold(self, sparse_trades):
        """Sharpe must be None when fewer than SHARPE_MIN_TRADES (90) exist."""
        m = compute_metrics(sparse_trades)
        assert m is not None
        assert m.sharpe_ratio is None, "CLAUDE.md rule 5: Sharpe must not be estimated from sparse data"

    def test_sharpe_is_computed_above_threshold(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        # 120 trades with P&L — should compute Sharpe
        assert m.sharpe_ratio is not None

    def test_profit_factor_is_positive(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        assert m.profit_factor is not None
        assert m.profit_factor > 0

    def test_profit_factor_none_when_no_losses(self, wallet_address):
        trades = [
            Trade(
                wallet_address=wallet_address,
                market_id=f"m{i}",
                side="BUY",
                size=100,
                price=0.5,
                pnl=50.0,
                is_resolved=True,
                timestamp=datetime.utcnow() + timedelta(hours=i),
            )
            for i in range(110)
        ]
        m = compute_metrics(trades)
        # No losses → profit_factor cannot be computed (division by zero)
        assert m.profit_factor is None

    def test_total_volume_sums_all_trade_sizes(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        expected = sum(t.size for t in profitable_trades)
        assert m.total_volume == pytest.approx(expected)

    def test_market_count_counts_unique_markets(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        assert m.market_count == 10  # fixture has 10 distinct markets

    def test_top_market_concentration(self, single_market_trades):
        m = compute_metrics(single_market_trades)
        assert m is not None
        assert m.top_market_concentration == pytest.approx(1.0)  # all in one market

    def test_win_count_loss_count_consistent(self, profitable_trades):
        m = compute_metrics(profitable_trades)
        assert m is not None
        assert m.win_count + m.loss_count <= m.trade_count


# ── apply_hard_filters ────────────────────────────────────────────────────────

class TestApplyHardFilters:
    def test_passes_qualifying_wallet(self, basic_metrics):
        result = apply_hard_filters(
            [basic_metrics],
            min_trades=100,
            min_win_rate=0.60,
            min_volume=5000.0,
        )
        assert len(result) == 1

    def test_rejects_insufficient_trades(self, basic_metrics):
        basic_metrics.trade_count = 50
        result = apply_hard_filters([basic_metrics], min_trades=100)
        assert len(result) == 0

    def test_rejects_low_win_rate(self, basic_metrics):
        basic_metrics.win_rate = 0.45
        result = apply_hard_filters([basic_metrics], min_win_rate=0.60)
        assert len(result) == 0

    def test_rejects_low_volume(self, basic_metrics):
        basic_metrics.total_volume = 1000.0
        result = apply_hard_filters([basic_metrics], min_volume=5000.0)
        assert len(result) == 0

    def test_rejects_none_win_rate(self, basic_metrics):
        basic_metrics.win_rate = None
        result = apply_hard_filters([basic_metrics])
        assert len(result) == 0

    def test_empty_list_returns_empty(self):
        assert apply_hard_filters([]) == []

    def test_filters_multiple_wallets(self, wallet_address, basic_metrics):
        from data.schema import WalletMetrics

        poor = WalletMetrics(
            wallet_address="0xpoor",
            trade_count=50,
            win_rate=0.40,
            total_volume=1000.0,
            computed_at=datetime.utcnow(),
        )
        result = apply_hard_filters([basic_metrics, poor])
        assert len(result) == 1
        assert result[0].wallet_address == wallet_address
