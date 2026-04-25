from __future__ import annotations

from datetime import datetime

import pytest

from data.schema import Position, WalletMetrics


def _make_position(
    wallet: str,
    condition_id: str,
    title: str | None = None,
    outcome: str = "Yes",
    size: float = 100.0,
    avg_price: float = 0.5,
    initial_value: float = 100.0,
    cash_pnl: float | None = None,
    redeemable: bool = False,
    asset: str | None = None,
) -> Position:
    return Position(
        wallet_address=wallet,
        condition_id=condition_id,
        asset=asset or f"0xasset_{condition_id}",
        title=title or f"Will {condition_id} happen?",
        outcome=outcome,
        size=size,
        avg_price=avg_price,
        initial_value=initial_value,
        current_value=initial_value * (1 + (cash_pnl or 0) / initial_value) if initial_value else None,
        cash_pnl=cash_pnl,
        redeemable=redeemable,
    )


@pytest.fixture
def wallet_address() -> str:
    return "0xabcdef1234567890abcdef1234567890abcdef12"


@pytest.fixture
def resolved_positions(wallet_address: str) -> list[Position]:
    """40 resolved positions across 10 markets — passes hard filters."""
    positions = []
    for i in range(40):
        positions.append(
            _make_position(
                wallet=wallet_address,
                condition_id=f"market_{i % 10}",
                title=f"Market question {i % 10}",
                outcome="Yes" if i % 2 == 0 else "No",
                size=200.0,
                avg_price=0.4 + (i % 5) * 0.05,
                initial_value=200.0,
                cash_pnl=50.0 if i % 3 != 0 else -20.0,
                redeemable=True,
            )
        )
    return positions


@pytest.fixture
def mixed_positions(wallet_address: str) -> list[Position]:
    """50 positions: 35 resolved, 15 unresolved, across 8 markets."""
    positions = []
    for i in range(50):
        positions.append(
            _make_position(
                wallet=wallet_address,
                condition_id=f"market_{i % 8}",
                size=150.0,
                initial_value=150.0,
                cash_pnl=30.0 if i % 2 == 0 else -10.0,
                redeemable=(i < 35),
            )
        )
    return positions


@pytest.fixture
def sparse_positions(wallet_address: str) -> list[Position]:
    """15 positions — below the MIN_TRADES threshold of 30."""
    return [
        _make_position(
            wallet=wallet_address,
            condition_id=f"market_{i % 3}",
            size=100.0,
            cash_pnl=30.0,
            redeemable=True,
        )
        for i in range(15)
    ]


@pytest.fixture
def single_market_positions(wallet_address: str) -> list[Position]:
    """40 positions all on the same market — triggers market_concentration."""
    return [
        _make_position(
            wallet=wallet_address,
            condition_id="market_only_one",
            size=100.0,
            cash_pnl=20.0 if i % 3 != 0 else -10.0,
            redeemable=(i % 2 == 0),
        )
        for i in range(40)
    ]


@pytest.fixture
def concentrated_pnl_positions(wallet_address: str) -> list[Position]:
    """35 positions where one outsized position dominates P&L."""
    positions = [
        _make_position(
            wallet=wallet_address,
            condition_id=f"market_{i}",
            size=100.0,
            cash_pnl=10.0,
            redeemable=True,
        )
        for i in range(34)
    ]
    # Add one outsized winner
    positions.append(
        _make_position(
            wallet=wallet_address,
            condition_id="market_big",
            size=5000.0,
            initial_value=5000.0,
            cash_pnl=80000.0,
            redeemable=True,
        )
    )
    return positions


@pytest.fixture
def basic_metrics(wallet_address: str) -> WalletMetrics:
    return WalletMetrics(
        wallet_address=wallet_address,
        trade_count=50,
        total_pnl=15000.0,
        total_volume=80000.0,
        market_count=12,
        top_market_concentration=0.15,
        portfolio_value=5000.0,
        realized_position_count=35,
        unresolved_position_count=15,
        avg_position_size=200.0,
        max_position_size_usd=2000.0,
        pct_pnl_from_top_3_positions=0.30,
        computed_at=datetime.utcnow(),
    )
