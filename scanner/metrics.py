from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np

from config import SHARPE_MIN_TRADES
from data.schema import Trade, WalletMetrics

logger = logging.getLogger(__name__)

# ── Trade parsing ──────────────────────────────────────────────────────────────

def parse_trades(wallet_address: str, raw: list[dict[str, Any]]) -> list[Trade]:
    """
    Convert raw API activity records into Trade ORM objects.
    Gracefully skips any records that are missing required fields.
    """
    trades: list[Trade] = []
    for record in raw:
        try:
            trade = _parse_one(wallet_address, record)
            if trade is not None:
                trades.append(trade)
        except Exception as exc:
            logger.debug("Skipping malformed trade record: %s — %s", record, exc)
    return trades


def _parse_one(wallet_address: str, r: dict[str, Any]) -> Trade | None:
    # Only process actual trade records, skip MERGE / SPLIT / REDEEM
    if r.get("type", "TRADE") not in ("TRADE", "BUY", "SELL", ""):
        return None

    # Timestamp — accept ISO string or Unix epoch int/float
    raw_ts = r.get("timestamp") or r.get("createdAt")
    if raw_ts is None:
        return None
    timestamp = _parse_timestamp(raw_ts)
    if timestamp is None:
        return None

    market_id = str(r.get("market") or r.get("conditionId") or r.get("marketId") or "")
    if not market_id:
        return None

    side = str(r.get("side") or r.get("type") or "BUY").upper()
    if side not in ("BUY", "SELL"):
        side = "BUY"

    size_raw = r.get("usdcSize") or r.get("amount") or r.get("size") or 0
    try:
        size = float(size_raw)
    except (TypeError, ValueError):
        size = 0.0

    price_raw = r.get("price") or r.get("avgPrice") or 0
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = 0.0

    pnl_raw = r.get("pnl") or r.get("profit")
    pnl: float | None = None
    if pnl_raw is not None:
        try:
            pnl = float(pnl_raw)
        except (TypeError, ValueError):
            pnl = None

    res_price_raw = r.get("resolutionPrice") or r.get("resolvedPrice")
    resolution_price: float | None = None
    if res_price_raw is not None:
        try:
            resolution_price = float(res_price_raw)
        except (TypeError, ValueError):
            resolution_price = None

    return Trade(
        wallet_address=wallet_address,
        market_id=market_id,
        market_question=r.get("title") or r.get("question"),
        side=side,
        outcome=r.get("outcome"),
        size=size,
        price=price,
        pnl=pnl,
        is_resolved=bool(r.get("resolved", False)),
        resolution_price=resolution_price,
        timestamp=timestamp,
    )


def _parse_timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        # Assume Unix seconds; if it looks like milliseconds, convert
        val = float(raw)
        if val > 1e12:
            val /= 1000
        return datetime.utcfromtimestamp(val)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        # Last resort: try fromisoformat
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return None


# ── Metric computation (pure functions) ───────────────────────────────────────

def compute_metrics(trades: list[Trade]) -> WalletMetrics | None:
    """
    Compute all statistics for a wallet's trade history.

    Returns None if the trade list is empty.
    Returns a WalletMetrics with None fields where data is insufficient —
    never fabricates numbers from incomplete data (CLAUDE.md rule 5).
    """
    if not trades:
        return None

    address = trades[0].wallet_address
    trade_count = len(trades)

    # Only count trades that have a known P&L outcome
    completed = [t for t in trades if t.pnl is not None]
    wins = [t for t in completed if t.pnl > 0]  # type: ignore[operator]
    losses = [t for t in completed if t.pnl < 0]  # type: ignore[operator]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / len(completed) if completed else None
    total_pnl = sum(t.pnl for t in completed) if completed else None  # type: ignore[misc]
    total_volume = sum(t.size for t in trades) if trades else None

    sharpe_ratio = _compute_sharpe(completed)
    profit_factor = _compute_profit_factor(wins, losses)
    avg_hold_time = _compute_avg_hold_time(trades)
    exit_quality = _compute_exit_quality(completed)

    # Market concentration
    market_counts: dict[str, int] = {}
    for t in trades:
        market_counts[t.market_id] = market_counts.get(t.market_id, 0) + 1
    market_count = len(market_counts)
    top_concentration = (
        max(market_counts.values()) / trade_count if market_counts else None
    )

    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_volume=total_volume,
        sharpe_ratio=sharpe_ratio,
        profit_factor=profit_factor,
        avg_hold_time_hours=avg_hold_time,
        exit_quality=exit_quality,
        market_count=market_count,
        top_market_concentration=top_concentration,
        computed_at=datetime.utcnow(),
    )


def _compute_sharpe(completed: list[Trade]) -> float | None:
    """Annualised Sharpe ratio on per-trade returns. None if < SHARPE_MIN_TRADES."""
    if len(completed) < SHARPE_MIN_TRADES:
        return None  # Per CLAUDE.md rule 5 — never estimate from sparse data

    returns = []
    for t in completed:
        if t.size and t.size > 0 and t.pnl is not None:
            returns.append(t.pnl / t.size)

    if len(returns) < 2:
        return None

    arr = np.array(returns, dtype=float)
    std = float(np.std(arr, ddof=1))
    if std == 0:
        return None

    # Annualise assuming ~252 effective trading periods
    return float(np.mean(arr) / std * np.sqrt(252))


def _compute_profit_factor(wins: list[Trade], losses: list[Trade]) -> float | None:
    gross_profit = sum(t.pnl for t in wins)  # type: ignore[misc]
    gross_loss = abs(sum(t.pnl for t in losses))  # type: ignore[misc]
    if gross_loss == 0:
        return None  # Division by zero — not a valid metric without losses
    return float(gross_profit / gross_loss)


def _compute_avg_hold_time(trades: list[Trade]) -> float | None:
    """
    Estimate average hold time by pairing BUY and SELL records on the same market.
    Returns hours. Returns None if no complete round-trips found.
    """
    # Group by market_id
    buys: dict[str, list[datetime]] = {}
    sells: dict[str, list[datetime]] = {}
    for t in sorted(trades, key=lambda x: x.timestamp):
        if t.side == "BUY":
            buys.setdefault(t.market_id, []).append(t.timestamp)
        elif t.side == "SELL":
            sells.setdefault(t.market_id, []).append(t.timestamp)

    hold_times: list[float] = []
    for market_id, buy_times in buys.items():
        sell_times = sells.get(market_id, [])
        pairs = min(len(buy_times), len(sell_times))
        for buy_ts, sell_ts in zip(buy_times[:pairs], sell_times[:pairs]):
            delta = (sell_ts - buy_ts).total_seconds() / 3600
            if delta >= 0:
                hold_times.append(delta)

    if not hold_times:
        return None
    return float(np.mean(hold_times))


def _compute_exit_quality(completed: list[Trade]) -> float | None:
    """
    For resolved markets, measure how close to the final settlement price
    the trader exited (or held to resolution).

    exit_quality = 1.0 means they captured 100% of the available move;
    lower values mean they left profit on the table.

    Returns None when no resolution data is available.
    """
    scores: list[float] = []
    for t in completed:
        if t.resolution_price is None:
            continue
        if t.resolution_price == 0:
            continue
        # For a winning BUY, ideal exit = 1.0 (YES resolved); measure how close they got
        captured_price = t.price if t.side == "SELL" else t.resolution_price
        score = min(1.0, captured_price / t.resolution_price)
        scores.append(score)

    return float(np.mean(scores)) if scores else None


# ── Hard filters ──────────────────────────────────────────────────────────────

def apply_hard_filters(
    metrics_list: list[WalletMetrics],
    min_trades: int | None = None,
    min_win_rate: float | None = None,
    min_volume: float | None = None,
) -> list[WalletMetrics]:
    """Return only wallets that pass the minimum quality thresholds."""
    from config import MIN_TRADES, MIN_VOLUME_USD, MIN_WIN_RATE

    min_trades = min_trades if min_trades is not None else MIN_TRADES
    min_win_rate = min_win_rate if min_win_rate is not None else MIN_WIN_RATE
    min_volume = min_volume if min_volume is not None else MIN_VOLUME_USD

    passed = []
    for m in metrics_list:
        if m.trade_count < min_trades:
            continue
        if m.win_rate is None or m.win_rate < min_win_rate:
            continue
        if m.total_volume is None or m.total_volume < min_volume:
            continue
        passed.append(m)

    logger.info(
        "Hard filters: %d → %d wallets (trades≥%d, win_rate≥%.0f%%, volume≥$%.0f)",
        len(metrics_list),
        len(passed),
        min_trades,
        min_win_rate * 100,
        min_volume,
    )
    return passed
