from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from data.schema import Trade

logger = logging.getLogger(__name__)


@dataclass
class WalletPatterns:
    """Extracted behavioural patterns for a wallet."""

    wallet_address: str

    # Hold-time stats (hours)
    avg_hold_hours: float | None = None
    median_hold_hours: float | None = None
    min_hold_hours: float | None = None
    max_hold_hours: float | None = None

    # Entry price range preference
    avg_entry_price: float | None = None
    low_probability_pct: float | None = None   # % of entries at price < 0.30
    high_probability_pct: float | None = None  # % of entries at price > 0.70

    # Position sizing (USDC)
    avg_position_size: float | None = None
    size_std: float | None = None
    large_position_pct: float | None = None   # % of trades > 10x median size

    # Time-of-day entry (UTC hour 0-23) — most common entry hour
    most_common_entry_hour: int | None = None

    # Market diversity
    unique_markets: int = 0
    top_3_market_share: float | None = None   # fraction in top 3 markets

    # Win/loss behaviour
    avg_winner_hold_hours: float | None = None
    avg_loser_hold_hours: float | None = None
    cuts_losses_quickly: bool | None = None   # loser hold < 0.5 * winner hold

    # Outcome preference
    yes_bet_pct: float | None = None   # fraction of BUY trades on "Yes"

    # Extra metadata
    market_questions: list[str] = field(default_factory=list)  # sample of top markets


def extract_patterns(trades: list[Trade]) -> WalletPatterns | None:
    if not trades:
        return None

    address = trades[0].wallet_address
    patterns = WalletPatterns(wallet_address=address)

    buys = [t for t in trades if t.side == "BUY"]
    sells = [t for t in trades if t.side == "SELL"]
    completed = [t for t in trades if t.pnl is not None]
    wins = [t for t in completed if t.pnl > 0]  # type: ignore[operator]
    losses = [t for t in completed if t.pnl < 0]  # type: ignore[operator]

    # ── Entry price preference ────────────────────────────────────────────────
    if buys:
        prices = [t.price for t in buys if 0 < t.price <= 1]
        if prices:
            patterns.avg_entry_price = float(np.mean(prices))
            patterns.low_probability_pct = sum(1 for p in prices if p < 0.30) / len(prices)
            patterns.high_probability_pct = sum(1 for p in prices if p > 0.70) / len(prices)

    # ── Position sizing ───────────────────────────────────────────────────────
    sizes = [t.size for t in trades if t.size > 0]
    if sizes:
        arr = np.array(sizes)
        patterns.avg_position_size = float(np.mean(arr))
        patterns.size_std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        median = float(np.median(arr))
        patterns.large_position_pct = sum(1 for s in sizes if s > 10 * median) / len(sizes)

    # ── Time-of-day ───────────────────────────────────────────────────────────
    entry_hours = [t.timestamp.hour for t in buys if isinstance(t.timestamp, datetime)]
    if entry_hours:
        hour_counts = Counter(entry_hours)
        patterns.most_common_entry_hour = hour_counts.most_common(1)[0][0]

    # ── Market diversity ──────────────────────────────────────────────────────
    market_counts: Counter[str] = Counter(t.market_id for t in trades)
    patterns.unique_markets = len(market_counts)
    if market_counts:
        top3 = sum(count for _, count in market_counts.most_common(3))
        patterns.top_3_market_share = top3 / len(trades)
        # Collect sample titles for top 3 markets
        top3_ids = {mid for mid, _ in market_counts.most_common(3)}
        seen: set[str] = set()
        for t in trades:
            if t.market_id in top3_ids and t.market_question and t.market_question not in seen:
                patterns.market_questions.append(t.market_question)
                seen.add(t.market_question)
            if len(patterns.market_questions) >= 3:
                break

    # ── Hold time (round-trip BUY → SELL per market) ─────────────────────────
    hold_times = _compute_hold_times(buys, sells)
    if hold_times:
        arr_ht = np.array(hold_times)
        patterns.avg_hold_hours = float(np.mean(arr_ht))
        patterns.median_hold_hours = float(np.median(arr_ht))
        patterns.min_hold_hours = float(np.min(arr_ht))
        patterns.max_hold_hours = float(np.max(arr_ht))

    # ── Win vs loss hold time comparison ─────────────────────────────────────
    winner_holds = _compute_hold_times(
        [t for t in buys if t.market_id in {w.market_id for w in wins}],
        [t for t in sells if t.market_id in {w.market_id for w in wins}],
    )
    loser_holds = _compute_hold_times(
        [t for t in buys if t.market_id in {l.market_id for l in losses}],
        [t for t in sells if t.market_id in {l.market_id for l in losses}],
    )
    if winner_holds:
        patterns.avg_winner_hold_hours = float(np.mean(winner_holds))
    if loser_holds:
        patterns.avg_loser_hold_hours = float(np.mean(loser_holds))
    if patterns.avg_winner_hold_hours and patterns.avg_loser_hold_hours:
        patterns.cuts_losses_quickly = (
            patterns.avg_loser_hold_hours < 0.5 * patterns.avg_winner_hold_hours
        )

    # ── Outcome preference ────────────────────────────────────────────────────
    yes_buys = [t for t in buys if (t.outcome or "").lower() in ("yes", "1")]
    if buys:
        patterns.yes_bet_pct = len(yes_buys) / len(buys)

    return patterns


def _compute_hold_times(
    buys: list[Trade], sells: list[Trade]
) -> list[float]:
    """Pair BUYs and SELLs per market_id (FIFO) and return hold durations in hours."""
    buy_q: dict[str, list[datetime]] = {}
    for t in sorted(buys, key=lambda x: x.timestamp):
        buy_q.setdefault(t.market_id, []).append(t.timestamp)

    hold_times: list[float] = []
    for t in sorted(sells, key=lambda x: x.timestamp):
        q = buy_q.get(t.market_id)
        if q:
            buy_ts = q.pop(0)
            hours = (t.timestamp - buy_ts).total_seconds() / 3600
            if hours >= 0:
                hold_times.append(hours)

    return hold_times


def patterns_to_dict(p: WalletPatterns) -> dict:
    """Serialise for Claude prompt injection."""
    return {
        "avg_hold_hours": p.avg_hold_hours,
        "median_hold_hours": p.median_hold_hours,
        "avg_entry_price": p.avg_entry_price,
        "low_prob_entry_pct": p.low_probability_pct,
        "high_prob_entry_pct": p.high_probability_pct,
        "avg_position_size_usdc": p.avg_position_size,
        "unique_markets": p.unique_markets,
        "top_3_market_share": p.top_3_market_share,
        "cuts_losses_quickly": p.cuts_losses_quickly,
        "yes_bet_pct": p.yes_bet_pct,
        "most_common_entry_hour_utc": p.most_common_entry_hour,
        "sample_top_markets": p.market_questions[:3],
    }
