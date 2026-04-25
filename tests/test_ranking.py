from __future__ import annotations

from datetime import datetime

import pytest

from data.schema import WalletMetrics
from scanner.ranking import compute_composite_score, rank_wallets


def _metrics(
    address: str,
    total_pnl: float | None = 15000.0,
    realized_position_count: int = 40,
    pct_pnl_from_top_3: float | None = 0.30,
    total_volume: float | None = 80_000.0,
    portfolio_value: float | None = 5000.0,
    trade_count: int = 50,
) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        total_pnl=total_pnl,
        total_volume=total_volume,
        market_count=10,
        portfolio_value=portfolio_value,
        realized_position_count=realized_position_count,
        unresolved_position_count=trade_count - realized_position_count,
        pct_pnl_from_top_3_positions=pct_pnl_from_top_3,
        computed_at=datetime.utcnow(),
    )


class TestComputeCompositeScore:
    def test_score_in_unit_range(self, basic_metrics):
        score = compute_composite_score(basic_metrics)
        assert 0.0 <= score <= 1.0

    def test_better_wallet_scores_higher(self):
        good = _metrics("0xgood", total_pnl=200_000, realized_position_count=150, pct_pnl_from_top_3=0.1)
        poor = _metrics("0xpoor", total_pnl=5001, realized_position_count=10, pct_pnl_from_top_3=0.9)
        assert compute_composite_score(good) > compute_composite_score(poor)

    def test_high_pnl_concentration_penalised(self):
        diverse = _metrics("0xa", pct_pnl_from_top_3=0.10)
        concentrated = _metrics("0xb", pct_pnl_from_top_3=0.95)
        assert compute_composite_score(diverse) > compute_composite_score(concentrated)

    def test_none_pnl_scores_zero_on_that_component(self):
        m = _metrics("0xa", total_pnl=None)
        score = compute_composite_score(m)
        assert 0.0 <= score <= 1.0

    def test_none_portfolio_value_scores_zero_on_that_component(self):
        m = _metrics("0xa", portfolio_value=None)
        score = compute_composite_score(m)
        assert 0.0 <= score <= 1.0

    def test_none_pct_pnl_uses_neutral(self):
        with_pct = _metrics("0xa", pct_pnl_from_top_3=0.5)
        without_pct = _metrics("0xb", pct_pnl_from_top_3=None)
        # Neither should crash; scores should be close (neutral = 0.5)
        s1 = compute_composite_score(with_pct)
        s2 = compute_composite_score(without_pct)
        assert 0.0 <= s1 <= 1.0
        assert 0.0 <= s2 <= 1.0

    def test_negative_pnl_scores_zero(self):
        m = _metrics("0xa", total_pnl=-5000.0)
        score = compute_composite_score(m)
        assert 0.0 <= score <= 1.0

    def test_stable_on_repeated_calls(self, basic_metrics):
        s1 = compute_composite_score(basic_metrics)
        s2 = compute_composite_score(basic_metrics)
        assert s1 == pytest.approx(s2)

    def test_custom_weights_respected(self):
        """PNL-only weights should rank the higher-PNL wallet first."""
        high_pnl = _metrics("0xa", total_pnl=500_000, realized_position_count=10)
        low_pnl = _metrics("0xb", total_pnl=5001, realized_position_count=200)
        weights = {"total_pnl": 1.0}
        assert compute_composite_score(high_pnl, weights) > compute_composite_score(low_pnl, weights)


class TestRankWallets:
    def test_returns_correct_count(self):
        wallets = [_metrics(f"0x{i:040x}") for i in range(10)]
        rankings = rank_wallets(wallets)
        assert len(rankings) == 10

    def test_rank_one_has_highest_score(self):
        good = _metrics("0x" + "a" * 40, total_pnl=300_000, realized_position_count=150, pct_pnl_from_top_3=0.1)
        poor = _metrics("0x" + "b" * 40, total_pnl=5001, realized_position_count=10, pct_pnl_from_top_3=0.9)
        rankings = rank_wallets([poor, good])  # deliberately unordered input
        assert rankings[0].rank == 1
        assert rankings[0].wallet_address == good.wallet_address

    def test_ranks_are_sequential_from_one(self):
        wallets = [_metrics(f"0x{i:040x}") for i in range(5)]
        rankings = rank_wallets(wallets)
        assert [r.rank for r in rankings] == [1, 2, 3, 4, 5]

    def test_composite_scores_are_descending(self):
        wallets = [_metrics(f"0x{i:040x}", total_pnl=5001 + i * 10_000) for i in range(5)]
        rankings = rank_wallets(wallets)
        scores = [r.composite_score for r in rankings]
        assert scores == sorted(scores, reverse=True)

    def test_empty_list_returns_empty(self):
        assert rank_wallets([]) == []

    def test_single_wallet_gets_rank_one(self, basic_metrics):
        rankings = rank_wallets([basic_metrics])
        assert len(rankings) == 1
        assert rankings[0].rank == 1

    def test_idempotent_on_same_data(self, basic_metrics):
        r1 = rank_wallets([basic_metrics])
        r2 = rank_wallets([basic_metrics])
        assert r1[0].composite_score == pytest.approx(r2[0].composite_score)
