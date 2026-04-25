from __future__ import annotations

import logging

from data.schema import WalletMetrics

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# >70% of P&L from top 3 positions by absolute value — luck rather than skill
PCT_PNL_TOP3_DOMINANCE = 0.70

# >70% of positions in a single market (and at least 20 positions)
MARKET_CONCENTRATION_THRESHOLD = 0.70
MARKET_CONCENTRATION_MIN_POSITIONS = 20

# Wallet looks good only because losing bets haven't resolved yet
SURVIVORSHIP_UNRESOLVED_RATIO = 3.0


# ── Individual detector functions (pure — no DB I/O) ─────────────────────────

def check_single_bet_dominance(metrics: WalletMetrics) -> bool:
    """
    Flag when the top 3 positions by absolute P&L account for more than 70%
    of total cash P&L. Genuine edge traders diversify; extreme P&L concentration
    suggests luck or a single outsized bet rather than repeatable skill.
    """
    return (
        metrics.pct_pnl_from_top_3_positions is not None
        and metrics.pct_pnl_from_top_3_positions > PCT_PNL_TOP3_DOMINANCE
    )


def check_market_concentration(metrics: WalletMetrics) -> bool:
    """
    Flag when more than 70% of all positions are concentrated in a single market.
    Different from single_bet_dominance — covers wallets trading one market repeatedly.
    """
    return (
        metrics.top_market_concentration is not None
        and metrics.top_market_concentration > MARKET_CONCENTRATION_THRESHOLD
        and metrics.market_count > 0
        and metrics.trade_count >= MARKET_CONCENTRATION_MIN_POSITIONS
    )


def check_survivorship(metrics: WalletMetrics) -> bool:
    """
    Flag when unresolved positions outnumber resolved positions by 3:1 or more.
    A wallet looks skilled only because its losing bets haven't settled yet.
    """
    if metrics.realized_position_count == 0:
        return False
    return (
        metrics.unresolved_position_count
        > metrics.realized_position_count * SURVIVORSHIP_UNRESOLVED_RATIO
    )


def check_recency_cliff(
    recent_win_rate: float | None,
    overall_win_rate: float | None,
    drop_threshold: float = 0.70,
) -> bool:
    """
    Flag when recent performance is substantially worse than historical.
    Caller must supply both rates; not computed from WalletMetrics alone.
    """
    if recent_win_rate is None or overall_win_rate is None:
        return False
    if overall_win_rate < 0.60:
        return False
    return recent_win_rate < overall_win_rate * drop_threshold


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

    if check_survivorship(metrics):
        flags.append("survivorship")

    if flags:
        logger.debug("Red flags for %s: %s", metrics.wallet_address, flags)

    return flags
