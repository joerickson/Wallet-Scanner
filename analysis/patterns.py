"""Behaviour pattern extraction.

This module previously extracted patterns from /activity trade records.
The scanner now uses /positions + /v1/leaderboard instead of /activity,
so trade-level pattern extraction is no longer part of the pipeline.
The module is retained for potential future use with position data.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WalletPatterns:
    """Placeholder for wallet behavioural patterns (position-based analysis TBD)."""

    wallet_address: str

    # Position size stats (USDC)
    avg_position_size: float | None = None
    max_position_size: float | None = None

    # Market diversity
    unique_markets: int = 0
    top_3_market_share: float | None = None

    # Resolution breakdown
    realized_count: int = 0
    unresolved_count: int = 0

    # Sample market titles
    market_titles: list[str] = field(default_factory=list)


def patterns_to_dict(p: WalletPatterns) -> dict:
    """Serialise for prompt injection."""
    return {
        "avg_position_size_usdc": p.avg_position_size,
        "max_position_size_usdc": p.max_position_size,
        "unique_markets": p.unique_markets,
        "top_3_market_share": p.top_3_market_share,
        "realized_count": p.realized_count,
        "unresolved_count": p.unresolved_count,
        "sample_markets": p.market_titles[:3],
    }
