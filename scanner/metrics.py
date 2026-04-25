from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np

from data.schema import Position, WalletMetrics

logger = logging.getLogger(__name__)


# ── Position parsing ───────────────────────────────────────────────────────────

def parse_positions(wallet_address: str, raw: list[dict[str, Any]]) -> list[Position]:
    """
    Convert raw /positions API records into Position ORM objects.
    Gracefully skips any records missing required fields.
    """
    positions: list[Position] = []
    for record in raw:
        try:
            pos = _parse_one_position(wallet_address, record)
            if pos is not None:
                positions.append(pos)
        except Exception as exc:
            logger.debug("Skipping malformed position record: %s — %s", record, exc)
    return positions


def _parse_one_position(wallet_address: str, r: dict[str, Any]) -> Position | None:
    condition_id = str(r.get("conditionId") or r.get("condition_id") or "")
    if not condition_id:
        return None

    end_date: datetime | None = None
    raw_end = r.get("endDate") or r.get("end_date")
    if raw_end:
        end_date = _parse_timestamp(raw_end)

    return Position(
        wallet_address=wallet_address,
        condition_id=condition_id,
        asset=r.get("asset"),
        title=r.get("title"),
        slug=r.get("slug"),
        outcome=r.get("outcome"),
        avg_price=_safe_float(r.get("avgPrice") or r.get("avg_price")),
        size=_safe_float(r.get("size")),
        initial_value=_safe_float(r.get("initialValue") or r.get("initial_value")),
        current_value=_safe_float(r.get("currentValue") or r.get("current_value")),
        cash_pnl=_safe_float(r.get("cashPnl") or r.get("cash_pnl")),
        percent_pnl=_safe_float(r.get("percentPnl") or r.get("percent_pnl")),
        total_bought=_safe_float(r.get("totalBought") or r.get("total_bought")),
        realized_pnl=_safe_float(r.get("realizedPnl") or r.get("realized_pnl")),
        percent_realized_pnl=_safe_float(r.get("percentRealizedPnl") or r.get("percent_realized_pnl")),
        current_price=_safe_float(r.get("curPrice") or r.get("current_price")),
        redeemable=bool(r.get("redeemable", False)),
        end_date=end_date,
    )


def _parse_timestamp(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1e12:
            val /= 1000
        return datetime.utcfromtimestamp(val)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Metric computation (pure functions) ───────────────────────────────────────

def compute_metrics(
    positions: list[Position],
    leaderboard_pnl: float | None,
    leaderboard_vol: float | None,
    portfolio_value: float | None,
) -> WalletMetrics | None:
    """
    Compute wallet statistics from positions + leaderboard data.

    Returns None if there are no positions.
    total_pnl and total_volume come from the Polymarket leaderboard — authoritative.
    """
    if not positions:
        return None

    address = positions[0].wallet_address
    trade_count = len(positions)

    realized_positions = [p for p in positions if p.redeemable]
    unresolved_positions = [p for p in positions if not p.redeemable]

    # Market concentration (by condition_id)
    condition_ids = [p.condition_id for p in positions]
    market_count = len(set(condition_ids))
    condition_counts: dict[str, int] = {}
    for cid in condition_ids:
        condition_counts[cid] = condition_counts.get(cid, 0) + 1
    top_concentration = max(condition_counts.values()) / trade_count if condition_counts else None

    # Position size stats
    sizes = [p.size for p in positions if p.size is not None and p.size > 0]
    avg_position_size = float(np.mean(sizes)) if sizes else None

    initial_values = [p.initial_value for p in positions if p.initial_value is not None]
    max_position_size_usd = max(initial_values) if initial_values else None

    # P&L concentration: top-3 by absolute cash_pnl / total cash_pnl
    pct_pnl_from_top_3 = _compute_pct_pnl_top_3(positions)

    return WalletMetrics(
        wallet_address=address,
        trade_count=trade_count,
        total_pnl=leaderboard_pnl,
        total_volume=leaderboard_vol,
        market_count=market_count,
        top_market_concentration=top_concentration,
        portfolio_value=portfolio_value,
        realized_position_count=len(realized_positions),
        unresolved_position_count=len(unresolved_positions),
        avg_position_size=avg_position_size,
        max_position_size_usd=max_position_size_usd,
        pct_pnl_from_top_3_positions=pct_pnl_from_top_3,
        computed_at=datetime.utcnow(),
    )


def _compute_pct_pnl_top_3(positions: list[Position]) -> float | None:
    """
    Concentration metric: sum of cashPnl from top-3-by-absolute-value positions
    divided by total cashPnl. Returns None when total cashPnl is zero.
    """
    positions_with_pnl = [p for p in positions if p.cash_pnl is not None]
    if not positions_with_pnl:
        return None

    total_cash_pnl = sum(p.cash_pnl for p in positions_with_pnl)  # type: ignore[misc]
    if total_cash_pnl == 0:
        return None

    top_3 = sorted(positions_with_pnl, key=lambda p: abs(p.cash_pnl), reverse=True)[:3]  # type: ignore[arg-type]
    top_3_pnl = sum(p.cash_pnl for p in top_3)  # type: ignore[misc]
    return float(top_3_pnl / total_cash_pnl)


# ── Hard filters ──────────────────────────────────────────────────────────────

def apply_hard_filters(
    metrics_list: list[WalletMetrics],
    min_trades: int | None = None,
    min_pnl: float | None = None,
    min_volume: float | None = None,
    min_realized_positions: int | None = None,
) -> list[WalletMetrics]:
    """Return only wallets that pass the minimum quality thresholds."""
    from config import MIN_PNL, MIN_REALIZED_POSITIONS, MIN_TRADES, MIN_VOLUME_USD

    min_trades = min_trades if min_trades is not None else MIN_TRADES
    min_pnl = min_pnl if min_pnl is not None else MIN_PNL
    min_volume = min_volume if min_volume is not None else MIN_VOLUME_USD
    min_realized_positions = (
        min_realized_positions if min_realized_positions is not None else MIN_REALIZED_POSITIONS
    )

    passed = []
    for m in metrics_list:
        if m.trade_count < min_trades:
            continue
        if m.total_pnl is None or m.total_pnl < min_pnl:
            continue
        if m.total_volume is None or m.total_volume < min_volume:
            continue
        if m.realized_position_count < min_realized_positions:
            continue
        passed.append(m)

    logger.info(
        "Hard filters: %d → %d wallets "
        "(positions≥%d, pnl≥$%.0f, volume≥$%.0f, realized≥%d)",
        len(metrics_list),
        len(passed),
        min_trades,
        min_pnl,
        min_volume,
        min_realized_positions,
    )
    return passed
