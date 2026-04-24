from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

from config import RANKING_WEIGHTS
from data.schema import WalletMetrics, WalletRanking

logger = logging.getLogger(__name__)

# Normalisation bounds — chosen so that "good" wallets land in the 0.5-1.0 range
_WIN_RATE_BOUNDS = (0.0, 1.0)
_SHARPE_BOUNDS = (-2.0, 5.0)
_PROFIT_FACTOR_BOUNDS = (0.0, 5.0)
_PNL_MAX_LOG = np.log1p(100_000)  # normalise up to $100k log-scale
_TRADE_COUNT_BOUNDS = (100, 2_000)


def _normalise(value: float, low: float, high: float) -> float:
    """Clip and scale value to [0, 1]."""
    if high == low:
        return 0.0
    return float(max(0.0, min(1.0, (value - low) / (high - low))))


def compute_composite_score(
    metrics: WalletMetrics, weights: dict[str, float] | None = None
) -> float:
    """
    Produce a single [0, 1] composite score for a wallet.
    Components with None values are omitted; remaining weights are renormalised.
    """
    weights = weights or RANKING_WEIGHTS
    components: dict[str, float] = {}

    if metrics.win_rate is not None:
        components["win_rate"] = _normalise(
            metrics.win_rate, *_WIN_RATE_BOUNDS
        )

    if metrics.sharpe_ratio is not None:
        components["sharpe"] = _normalise(
            metrics.sharpe_ratio, *_SHARPE_BOUNDS
        )

    if metrics.profit_factor is not None:
        components["profit_factor"] = _normalise(
            metrics.profit_factor, *_PROFIT_FACTOR_BOUNDS
        )

    if metrics.total_pnl is not None and metrics.total_pnl > 0:
        components["total_pnl"] = min(
            1.0, float(np.log1p(metrics.total_pnl)) / _PNL_MAX_LOG
        )
    else:
        components["total_pnl"] = 0.0

    if metrics.trade_count > 0:
        components["trade_count"] = _normalise(
            float(metrics.trade_count), *_TRADE_COUNT_BOUNDS
        )

    weighted_sum = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        if key in components:
            weighted_sum += components[key] * weight
            total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def rank_wallets(
    metrics_list: list[WalletMetrics],
    weights: dict[str, float] | None = None,
) -> list[WalletRanking]:
    """
    Score and rank all wallets in metrics_list.
    Returns a list of WalletRanking objects sorted best-first (rank=1 is best).
    """
    weights = weights or RANKING_WEIGHTS
    scored: list[tuple[float, WalletMetrics]] = []

    for metrics in metrics_list:
        score = compute_composite_score(metrics, weights)
        scored.append((score, metrics))

    # Stable sort: descending score, tie-break by win_rate
    scored.sort(key=lambda x: (x[0], x[1].win_rate or 0.0), reverse=True)

    now = datetime.utcnow()
    rankings: list[WalletRanking] = []
    for rank, (score, metrics) in enumerate(scored, start=1):
        rankings.append(
            WalletRanking(
                wallet_address=metrics.wallet_address,
                composite_score=score,
                rank=rank,
                ranked_at=now,
            )
        )

    logger.info("Ranked %d wallets (top score=%.4f)", len(rankings), scored[0][0] if scored else 0)
    return rankings
