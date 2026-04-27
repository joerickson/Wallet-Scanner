from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
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
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True)


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
    """Per-user watchlist entries referencing neon_auth.user by user_id."""

    __tablename__ = "user_watchlist"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)  # id from neon_auth.user (Better Auth)
    wallet_address: str = Field(foreign_key="wallet.address", index=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = Field(default=None)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "wallet_address"),)


class WalletStrategyAnalysis(SQLModel, table=True):
    """Deep Claude strategy analysis for a wallet — tracks replicability over time."""

    __tablename__ = "wallet_strategy_analysis"

    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(foreign_key="wallet.address", index=True)

    # Replicability assessment
    is_replicable: bool
    replicability_confidence: float
    capital_required_min_usd: Optional[int] = Field(default=None)

    # Strategy classification
    strategy_type: str
    strategy_subtype: Optional[str] = Field(default=None)

    # Replication blueprint
    entry_signal: str
    exit_signal: str
    position_sizing_rule: str
    market_selection_criteria: str
    infrastructure_required: str

    # Performance characterization
    estimated_hit_rate: Optional[float] = Field(default=None)
    estimated_avg_hold_time_hours: Optional[float] = Field(default=None)
    estimated_sharpe_proxy: Optional[float] = Field(default=None)

    # Risk assessment (JSON-encoded list[str])
    failure_modes: str = Field(default="[]")
    risk_factors: str = Field(default="[]")

    # Meta
    prompt_version: str
    model_used: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    wallet_state_snapshot: str = Field(default="{}")  # JSON-encoded dict

    # Long-form sections
    full_thesis: str
    paper_trade_recommendation: str

    # Structured machine-readable filter derived from paper_trade_recommendation (JSON-encoded)
    paper_test_filter: Optional[str] = Field(default=None)

    __table_args__ = (Index("ix_strategy_wallet_generated", "wallet_address", "generated_at"),)


class ClaudeUsageLog(SQLModel, table=True):
    """Tracks every Claude API call for cost monitoring."""

    __tablename__ = "claude_usage_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    call_type: str  # "strategy_analysis" | "scanner_review"
    wallet_address: Optional[str] = Field(default=None)
    model_used: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    logged_at: datetime = Field(default_factory=datetime.utcnow)


class PaperTest(SQLModel, table=True):
    """A paper trading session that simulates a wallet strategy against live Polymarket data."""

    __tablename__ = "paper_tests"

    id: str = Field(primary_key=True)
    wallet_address: str = Field(index=True)
    strategy_analysis_id: int = Field(index=True)
    user_id: str = Field(index=True)
    capital_allocated: float = Field(default=10000.0)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ends_at: datetime
    status: str = Field(default="running")  # running | completed | failed
    realized_pnl: float = Field(default=0.0)
    unrealized_pnl: float = Field(default=0.0)
    last_evaluated_at: Optional[datetime] = Field(default=None)
    filter_snapshot: str = Field(default="{}")  # JSON-encoded dict
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PaperTrade(SQLModel, table=True):
    """An individual simulated trade within a paper test session."""

    __tablename__ = "paper_trades"

    id: str = Field(primary_key=True)
    paper_test_id: str = Field(foreign_key="paper_tests.id", index=True)
    polymarket_condition_id: str
    market_question: str
    outcome_name: str
    token_id: str
    side: str  # 'buy' or 'sell'
    entry_price: float
    entry_size_usd: float
    entry_at: datetime = Field(default_factory=datetime.utcnow)
    exit_price: Optional[float] = Field(default=None)
    exit_at: Optional[datetime] = Field(default=None)
    exit_reason: Optional[str] = Field(default=None)  # resolution | price_move | time | manual
    realized_pnl: Optional[float] = Field(default=None)
    status: str = Field(default="open")  # open | closed
