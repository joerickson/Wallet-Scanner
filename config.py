from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Polymarket API ────────────────────────────────────────────────────────────
POLYMARKET_DATA_API_BASE: str = os.getenv(
    "POLYMARKET_DATA_API_BASE", "https://data-api.polymarket.com"
)
POLYMARKET_GAMMA_API_BASE: str = os.getenv(
    "POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com"
)

# ── Claude API ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
# Model for high-volume scanning (~200 calls per leaderboard refresh)
CLAUDE_SCANNER_MODEL: str = "claude-sonnet-4-20250514"
# Model for periodic deep analysis only — never used in scanner
CLAUDE_DEEP_MODEL: str = "claude-opus-4-7"
CLAUDE_MAX_TOKENS: int = 1024

# ── Rate limiting ─────────────────────────────────────────────────────────────
API_RATE_LIMIT: float = float(os.getenv("API_RATE_LIMIT", "2.0"))  # req/sec

# ── Hard scanner filters ──────────────────────────────────────────────────────
MIN_TRADES: int = int(os.getenv("MIN_TRADES", "100"))
MIN_WIN_RATE: float = float(os.getenv("MIN_WIN_RATE", "0.60"))
MIN_VOLUME_USD: float = float(os.getenv("MIN_VOLUME_USD", "5000.0"))
# Per CLAUDE.md rule 5: Sharpe is None for wallets with fewer than this many trades
SHARPE_MIN_TRADES: int = 90

# ── Claude review ─────────────────────────────────────────────────────────────
# Never call Claude on the full population — only on post-filter top N
CLAUDE_REVIEW_TOP_N: int = int(os.getenv("CLAUDE_REVIEW_TOP_N", "200"))

# ── Composite ranking weights ─────────────────────────────────────────────────
RANKING_WEIGHTS: dict[str, float] = {
    "win_rate": float(os.getenv("WEIGHT_WIN_RATE", "0.30")),
    "sharpe": float(os.getenv("WEIGHT_SHARPE", "0.25")),
    "profit_factor": float(os.getenv("WEIGHT_PROFIT_FACTOR", "0.20")),
    "total_pnl": float(os.getenv("WEIGHT_TOTAL_PNL", "0.15")),
    "trade_count": float(os.getenv("WEIGHT_TRADE_COUNT", "0.10")),
}

# ── Database ──────────────────────────────────────────────────────────────────
DATA_DIR: Path = Path(__file__).parent / "data"
try:
    DATA_DIR.mkdir(exist_ok=True)
except OSError:
    pass  # read-only filesystem (e.g. Vercel) — DATABASE_URL env var must be set
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/research.db")

# ── Turso (optional — replaces local SQLite when running in CI/cloud) ─────────
# Set both to use Turso instead of SQLite. Leave blank for local development.
TURSO_DATABASE_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
# Wallet is considered fresh for 24 h; re-scanned in incremental mode if older
WALLET_CACHE_TTL: int = int(os.getenv("WALLET_CACHE_TTL", "86400"))
API_CACHE_TTL: int = int(os.getenv("API_CACHE_TTL", "3600"))

# ── Watch / alert ─────────────────────────────────────────────────────────────
POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "300"))  # 5 minutes

# ── Webhooks (optional) ───────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
