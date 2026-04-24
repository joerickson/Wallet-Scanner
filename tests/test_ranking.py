from __future__ import annotations

from datetime import datetime

import pytest

from data.schema import WalletMetrics
from scanner.ranking import compute_composite_score, rank_wallets


def _metrics(
    address: str,
    win_rate: float | None = 0.65,
    sharpe: float | None = 1.5,
    profit_factor: float | None = 2.0,
    total_pnl: float | None = 5000.0,
    trade_count: int = 200,
    total_volume: float | None = 50_000.0,
) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        win_count=int((trade_count * (win_rate or 0))),
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_volume=total_volume,
        sharpe_ratio=sharpe,
        profit_factor=profit_factor,
        computed_at=datetime.utcnow(),
    )


class TestComputeCompositeScore:
    def test_score_in_unit_range(self, basic_metrics):
        score = compute_composite_score(basic_metrics)
        assert 0.0 <= score <= 1.0

    def test_better_wallet_scores_higher(self):
        good = _metrics("0xgood", win_rate=0.80, sharpe=3.0, profit_factor=4.0, total_pnl=50_000)
        poor = _metrics("0xpoor", win_rate=0.61, sharpe=0.5, profit_factor=1.1, total_pnl=500)
        assert compute_composite_score(good) > compute_composite_score(poor)

    def test_none_sharpe_still_scores(self):
        m = _metrics("0xa", sharpe=None)
        score = compute_composite_score(m)
        assert 0.0 <= score <= 1.0

    def test_all_none_components_gives_low_score(self):
        m = WalletMetrics(
            wallet_address="0xzero",
            trade_count=0,
            win_rate=None,
            total_pnl=None,
            total_volume=None,
            sharpe_ratio=None,
            profit_factor=None,
            computed_at=datetime.utcnow(),
        )
        score = compute_composite_score(m)
        # Even with all None, score should be 0 (trade_count=0 contributes 0)
        assert score == pytest.approx(0.0)

    def test_custom_weights_respected(self):
        """Win-rate-only weights should rank a high-win-rate wallet first."""
        high_wr = _metrics("0xa", win_rate=0.95, sharpe=0.5, profit_factor=1.1)
        low_wr = _metrics("0xb", win_rate=0.61, sharpe=3.0, profit_factor=5.0)
        weights = {"win_rate": 1.0}
        assert compute_composite_score(high_wr, weights) > compute_composite_score(low_wr, weights)

    def test_stable_on_repeated_calls(self, basic_metrics):
        s1 = compute_composite_score(basic_metrics)
        s2 = compute_composite_score(basic_metrics)
        assert s1 == pytest.approx(s2)

    def test_negative_pnl_does_not_crash(self):
        m = _metrics("0xa", total_pnl=-1000.0)
        score = compute_composite_score(m)
        assert 0.0 <= score <= 1.0


class TestRankWallets:
    def test_returns_correct_count(self):
        wallets = [_metrics(f"0x{i:040x}") for i in range(10)]
        rankings = rank_wallets(wallets)
        assert len(rankings) == 10

    def test_rank_one_has_highest_score(self):
        good = _metrics("0x" + "a" * 40, win_rate=0.90, sharpe=4.0, profit_factor=4.5)
        poor = _metrics("0x" + "b" * 40, win_rate=0.61, sharpe=0.3, profit_factor=1.1)
        rankings = rank_wallets([poor, good])  # deliberately unordered input
        assert rankings[0].rank == 1
        assert rankings[0].wallet_address == good.wallet_address

    def test_ranks_are_sequential_from_one(self):
        wallets = [_metrics(f"0x{i:040x}") for i in range(5)]
        rankings = rank_wallets(wallets)
        assert [r.rank for r in rankings] == [1, 2, 3, 4, 5]

    def test_composite_scores_are_descending(self):
        wallets = [_metrics(f"0x{i:040x}", win_rate=0.6 + i * 0.02) for i in range(5)]
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
