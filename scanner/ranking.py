from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

from config import RANKING_WEIGHTS
from data.schema import WalletMetrics, WalletRanking

logger = logging.getLogger(__name__)

# Normalisation bounds
_REALIZED_POSITIONS_BOUNDS = (0.0, 200.0)
_PCT_PNL_BOUNDS = (0.0, 1.0)
_PNL_MAX_LOG = np.log1p(500_000)      # normalise up to $500k
_VOLUME_MAX_LOG = np.log1p(1_000_000)  # normalise up to $1M
_PORTFOLIO_VALUE_MAX_LOG = np.log1p(100_000)  # normalise up to $100k


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

    Weights (from config.py RANKING_WEIGHTS):
      0.40 * normalized total_pnl
      0.20 * normalized realized_position_count  (more skill evidence = better)
      0.20 * inverse normalized pct_pnl_from_top_3_positions  (less concentration = better)
      0.10 * normalized total_volume
      0.10 * normalized portfolio_value
    """
    weights = weights or RANKING_WEIGHTS
    components: dict[str, float] = {}

    # Total P&L (log-normalised; negative P&L scores 0)
    if metrics.total_pnl is not None and metrics.total_pnl > 0:
        components["total_pnl"] = min(1.0, float(np.log1p(metrics.total_pnl)) / _PNL_MAX_LOG)
    else:
        components["total_pnl"] = 0.0

    # Realized position count (more resolved markets = more skill signal)
    components["realized_position_count"] = _normalise(
        float(metrics.realized_position_count), *_REALIZED_POSITIONS_BOUNDS
    )

    # Inverse P&L concentration (less concentrated = better diversification)
    if metrics.pct_pnl_from_top_3_positions is not None:
        raw_conc = _normalise(metrics.pct_pnl_from_top_3_positions, *_PCT_PNL_BOUNDS)
        components["pct_pnl_from_top_3_positions"] = 1.0 - raw_conc
    else:
        components["pct_pnl_from_top_3_positions"] = 0.5  # neutral if unknown

    # Total volume (log-normalised)
    if metrics.total_volume is not None and metrics.total_volume > 0:
        components["total_volume"] = min(1.0, float(np.log1p(metrics.total_volume)) / _VOLUME_MAX_LOG)
    else:
        components["total_volume"] = 0.0

    # Portfolio value (still active = good signal; log-normalised)
    if metrics.portfolio_value is not None and metrics.portfolio_value > 0:
        components["portfolio_value"] = min(
            1.0, float(np.log1p(metrics.portfolio_value)) / _PORTFOLIO_VALUE_MAX_LOG
        )
    else:
        components["portfolio_value"] = 0.0

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

    # Stable sort: descending score, tie-break by total_pnl
    scored.sort(key=lambda x: (x[0], x[1].total_pnl or 0.0), reverse=True)

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
