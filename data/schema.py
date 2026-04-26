from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Wallet(SQLModel, table=True):
    """Known trader addresses discovered from the Polymarket Data API."""

    __tablename__ = "wallet"

    address: str = Field(primary_key=True, index=True)
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_scanned: Optional[datetime] = Field(default=None)
    is_watched: bool = Field(default=False)


class Position(SQLModel, table=True):
    """Per-position data fetched from the /positions endpoint.

    redeemable=True means the market resolved and the position is redeemable (resolved).
    """

    __tablename__ = "position"

    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(index=True)
    condition_id: str = Field(index=True)
    asset: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    slug: Optional[str] = Field(default=None)
    outcome: Optional[str] = Field(default=None)
    avg_price: Optional[float] = Field(default=None)
    size: Optional[float] = Field(default=None)
    initial_value: Optional[float] = Field(default=None)
    current_value: Optional[float] = Field(default=None)
    cash_pnl: Optional[float] = Field(default=None)
    percent_pnl: Optional[float] = Field(default=None)
    total_bought: Optional[float] = Field(default=None)
    realized_pnl: Optional[float] = Field(default=None)
    percent_realized_pnl: Optional[float] = Field(default=None)
    current_price: Optional[float] = Field(default=None)
    redeemable: bool = Field(default=False)
    end_date: Optional[datetime] = Field(default=None)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class WalletMetrics(SQLModel, table=True):
    """Computed statistics for a wallet, derived from leaderboard + positions data."""

    __tablename__ = "walletmetrics"

    wallet_address: str = Field(primary_key=True)
    trade_count: int = Field(default=0)  # count of positions
    total_pnl: Optional[float] = Field(default=None)  # from leaderboard
    total_volume: Optional[float] = Field(default=None)  # from leaderboard
    market_count: int = Field(default=0)  # distinct condition_ids in positions
    top_market_concentration: Optional[float] = Field(default=None)
    portfolio_value: Optional[float] = Field(default=None)  # from /value endpoint
    realized_position_count: int = Field(default=0)  # positions where redeemable=True
    unresolved_position_count: int = Field(default=0)  # positions where redeemable=False
    avg_position_size: Optional[float] = Field(default=None)  # mean of position.size
    max_position_size_usd: Optional[float] = Field(default=None)  # max of initial_value
    pct_pnl_from_top_3_positions: Optional[float] = Field(default=None)
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


class UserWatchlist(SQLModel, table=True):
    """Per-user watchlist entries referencing neon_auth.users_sync by user_id."""

    __tablename__ = "user_watchlist"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)  # id from neon_auth.users_sync
    wallet_address: str = Field(foreign_key="wallet.address", index=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = Field(default=None)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "wallet_address"),)
