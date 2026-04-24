from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.schema import Trade, WalletMetrics


def _make_trade(
    wallet: str,
    market: str,
    side: str = "BUY",
    size: float = 100.0,
    price: float = 0.5,
    pnl: float | None = None,
    timestamp: datetime | None = None,
    outcome: str | None = "Yes",
    is_resolved: bool = False,
    resolution_price: float | None = None,
) -> Trade:
    return Trade(
        wallet_address=wallet,
        market_id=market,
        market_question=f"Will {market} happen?",
        side=side,
        outcome=outcome,
        size=size,
        price=price,
        pnl=pnl,
        is_resolved=is_resolved,
        resolution_price=resolution_price,
        timestamp=timestamp or datetime.utcnow(),
    )


@pytest.fixture
def wallet_address() -> str:
    return "0xabcdef1234567890abcdef1234567890abcdef12"


@pytest.fixture
def profitable_trades(wallet_address: str) -> list[Trade]:
    """60 winning trades and 10 losing — above filter thresholds."""
    base_time = datetime(2024, 1, 1)
    trades = []
    for i in range(120):
        ts = base_time + timedelta(hours=i * 6)
        pnl = 50.0 if i % 2 == 0 else -20.0  # ~60% win rate
        trades.append(
            _make_trade(
                wallet=wallet_address,
                market=f"market_{i % 10}",  # 10 distinct markets
                side="BUY" if i % 3 != 0 else "SELL",
                size=200.0,
                price=0.4 + (i % 5) * 0.05,
                pnl=pnl,
                timestamp=ts,
                is_resolved=True,
                resolution_price=1.0 if pnl > 0 else 0.0,
            )
        )
    return trades


@pytest.fixture
def sparse_trades(wallet_address: str) -> list[Trade]:
    """50 trades — below the MIN_TRADES threshold of 100."""
    base = datetime(2024, 1, 1)
    return [
        _make_trade(
            wallet=wallet_address,
            market=f"market_{i % 3}",
            size=100.0,
            price=0.5,
            pnl=30.0,
            timestamp=base + timedelta(hours=i * 12),
        )
        for i in range(50)
    ]


@pytest.fixture
def single_market_trades(wallet_address: str) -> list[Trade]:
    """110 trades all on the same market — triggers single_bet_dominance."""
    base = datetime(2024, 1, 1)
    return [
        _make_trade(
            wallet=wallet_address,
            market="market_only_one",
            size=100.0,
            price=0.5,
            pnl=20.0 if i % 3 != 0 else -10.0,
            timestamp=base + timedelta(hours=i * 4),
        )
        for i in range(110)
    ]


@pytest.fixture
def high_win_rate_sparse(wallet_address: str) -> list[Trade]:
    """150 trades with 95% win rate — triggers survivorship_bias."""
    base = datetime(2024, 1, 1)
    return [
        _make_trade(
            wallet=wallet_address,
            market=f"market_{i % 20}",
            size=100.0,
            price=0.5,
            pnl=50.0 if i < 142 else -10.0,  # 95% win rate
            timestamp=base + timedelta(hours=i * 6),
        )
        for i in range(150)
    ]


@pytest.fixture
def basic_metrics(wallet_address: str) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=wallet_address,
        trade_count=120,
        win_count=72,
        loss_count=48,
        win_rate=0.60,
        total_pnl=2500.0,
        total_volume=24000.0,
        sharpe_ratio=1.5,
        profit_factor=2.1,
        market_count=10,
        top_market_concentration=0.15,
        computed_at=datetime.utcnow(),
    )
