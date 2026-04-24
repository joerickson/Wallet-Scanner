from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Wallet(SQLModel, table=True):
    """Known trader addresses discovered from the Polymarket Data API."""

    __tablename__ = "wallet"

    address: str = Field(primary_key=True, index=True)
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_scanned: Optional[datetime] = Field(default=None)
    is_watched: bool = Field(default=False)


class Trade(SQLModel, table=True):
    """Individual trade records fetched from the activity endpoint."""

    __tablename__ = "trade"

    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(index=True)
    market_id: str = Field(index=True)
    market_question: Optional[str] = Field(default=None)
    side: str  # "BUY" or "SELL"
    outcome: Optional[str] = Field(default=None)  # "Yes" or "No"
    size: float  # USDC amount
    price: float  # 0.0 – 1.0
    pnl: Optional[float] = Field(default=None)  # None until resolved or sold
    is_resolved: bool = Field(default=False)
    resolution_price: Optional[float] = Field(default=None)
    timestamp: datetime
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class WalletMetrics(SQLModel, table=True):
    """Computed statistics for a wallet, refreshed each scan."""

    __tablename__ = "walletmetrics"

    wallet_address: str = Field(primary_key=True)
    trade_count: int = Field(default=0)
    win_count: int = Field(default=0)
    loss_count: int = Field(default=0)
    win_rate: Optional[float] = Field(default=None)
    total_pnl: Optional[float] = Field(default=None)
    total_volume: Optional[float] = Field(default=None)
    sharpe_ratio: Optional[float] = Field(default=None)  # None if < SHARPE_MIN_TRADES
    profit_factor: Optional[float] = Field(default=None)
    avg_hold_time_hours: Optional[float] = Field(default=None)
    exit_quality: Optional[float] = Field(default=None)  # None if data unavailable
    market_count: int = Field(default=0)
    top_market_concentration: Optional[float] = Field(default=None)
    computed_at: datetime = Field(default_factory=datetime.utcnow)


class WalletRanking(SQLModel, table=True):
    """Composite rank plus Claude qualitative review for each wallet."""

    __tablename__ = "walletranking"

    wallet_address: str = Field(primary_key=True)
    composite_score: float = Field(default=0.0)
    rank: int = Field(default=0)
    # Claude review fields (populated after qualitative pass)
    skill_signal: Optional[float] = Field(default=None)
    edge_hypothesis: Optional[str] = Field(default=None)
    claude_red_flags: Optional[str] = Field(default=None)  # JSON-encoded list[str]
    claude_notes: Optional[str] = Field(default=None)
    # Heuristic red flags (populated by red_flags module)
    heuristic_red_flags: Optional[str] = Field(default=None)  # JSON-encoded list[str]
    ranked_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)


class WatchedWallet(SQLModel, table=True):
    """Wallets added to the active watch list for position polling."""

    __tablename__ = "watchedwallet"

    wallet_address: str = Field(primary_key=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    last_position_check: Optional[datetime] = Field(default=None)
    # JSON snapshot of last known positions — used to diff for new alerts
    known_positions: Optional[str] = Field(default=None)


class Alert(SQLModel, table=True):
    """Alert records written when watched wallets take new positions."""

    __tablename__ = "alert"

    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(index=True)
    alert_type: str  # "new_position" | "closed_position" | "large_position"
    market_id: str
    market_question: Optional[str] = Field(default=None)
    side: Optional[str] = Field(default=None)
    size: Optional[float] = Field(default=None)
    price: Optional[float] = Field(default=None)
    details: Optional[str] = Field(default=None)  # JSON blob for extra context
    alerted_at: datetime = Field(default_factory=datetime.utcnow)
