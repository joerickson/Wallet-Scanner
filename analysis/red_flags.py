from __future__ import annotations

import logging

from data.schema import WalletMetrics

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# All thresholds chosen to target <5% false-positive rate on manual spot-checks

SINGLE_BET_DOMINANCE_THRESHOLD = 0.50   # >50% of all bets on one market
MARKET_CONCENTRATION_MAX_MARKETS = 3    # fewer than 3 distinct markets
MARKET_CONCENTRATION_MIN_TRADES = 20    # only flag if wallet has enough trades
SURVIVORSHIP_WIN_RATE_FLOOR = 0.90      # >90% win rate
SURVIVORSHIP_MAX_TRADES = 200           # on fewer than 200 completed trades
VOLUME_ROI_CEILING = 3.0               # P&L > 300% of total volume is suspicious


# ── Individual detector functions (pure — no DB I/O) ─────────────────────────

def check_single_bet_dominance(metrics: WalletMetrics) -> bool:
    """
    Flag when more than 50% of all bets are concentrated in a single market.
    Genuine edge traders diversify; extreme concentration suggests luck or inside info.
    """
    return (
        metrics.top_market_concentration is not None
        and metrics.top_market_concentration > SINGLE_BET_DOMINANCE_THRESHOLD
    )


def check_market_concentration(metrics: WalletMetrics) -> bool:
    """
    Flag when the wallet has fewer than 3 distinct markets despite enough trades.
    This is a different dimension from single-bet dominance — covers 2-market specialists.
    """
    return (
        metrics.market_count > 0
        and metrics.market_count < MARKET_CONCENTRATION_MAX_MARKETS
        and metrics.trade_count >= MARKET_CONCENTRATION_MIN_TRADES
    )


def check_survivorship_bias(metrics: WalletMetrics) -> bool:
    """
    Flag wallets with suspiciously high win rates on low trade counts.
    A 90%+ win rate is almost impossible to sustain legitimately; on fewer
    than 200 trades it may simply reflect cherry-picked history.
    """
    return (
        metrics.win_rate is not None
        and metrics.win_rate > SURVIVORSHIP_WIN_RATE_FLOOR
        and metrics.trade_count < SURVIVORSHIP_MAX_TRADES
    )


def check_volume_size_mismatch(metrics: WalletMetrics) -> bool:
    """
    Flag when reported P&L is implausibly large relative to total volume.
    ROI > 300% across all trades is a strong indicator of data artefact or fraud.
    """
    if (
        metrics.total_pnl is None
        or metrics.total_volume is None
        or metrics.total_volume == 0
    ):
        return False
    roi = metrics.total_pnl / metrics.total_volume
    return roi > VOLUME_ROI_CEILING


def check_recency_cliff(
    recent_win_rate: float | None,
    overall_win_rate: float | None,
    drop_threshold: float = 0.70,
) -> bool:
    """
    Flag when recent performance is substantially worse than historical.
    Indicates possible mean-reversion or that past results were luck.
    Not computed from WalletMetrics alone — caller must supply recent_win_rate.
    """
    if recent_win_rate is None or overall_win_rate is None:
        return False
    if overall_win_rate < 0.60:
        return False  # Only meaningful for wallets that passed the filter
    return recent_win_rate < overall_win_rate * drop_threshold


def check_insider_timing(avg_entry_time_after_open_hours: float | None) -> bool:
    """
    Flag when average entry is suspiciously within 1 hour of market open.
    Consistent early positioning may indicate non-public information.
    Not computed from WalletMetrics alone — caller must supply timing data.
    """
    return (
        avg_entry_time_after_open_hours is not None
        and avg_entry_time_after_open_hours < 1.0
    )


# ── Aggregated flag list ──────────────────────────────────────────────────────

def get_red_flags(metrics: WalletMetrics) -> list[str]:
    """
    Run all available heuristic detectors against a WalletMetrics object
    and return a list of triggered flag names.
    """
    flags: list[str] = []

    if check_single_bet_dominance(metrics):
        flags.append("single_bet_dominance")

    if check_market_concentration(metrics):
        flags.append("market_concentration")

    if check_survivorship_bias(metrics):
        flags.append("survivorship_bias")

    if check_volume_size_mismatch(metrics):
        flags.append("volume_size_mismatch")

    if flags:
        logger.debug("Red flags for %s: %s", metrics.wallet_address, flags)

    return flags
