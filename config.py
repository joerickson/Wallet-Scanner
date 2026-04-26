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
# trade_count = number of positions (not /activity events)
MIN_TRADES: int = int(os.getenv("MIN_TRADES", "30"))
# Minimum P&L from leaderboard — direct skill signal from Polymarket
MIN_PNL: float = float(os.getenv("MIN_PNL", "5000.0"))
MIN_VOLUME_USD: float = float(os.getenv("MIN_VOLUME_USD", "5000.0"))
# Must have at least this many resolved positions to be evaluable
MIN_REALIZED_POSITIONS: int = int(os.getenv("MIN_REALIZED_POSITIONS", "10"))

# ── Claude review ─────────────────────────────────────────────────────────────
# Never call Claude on the full population — only on post-filter top N
CLAUDE_REVIEW_TOP_N: int = int(os.getenv("CLAUDE_REVIEW_TOP_N", "200"))

# ── Strategy analysis ─────────────────────────────────────────────────────────
# Deep analysis runs on top N wallets only; Sonnet keeps cost within ~$10/month
STRATEGY_ANALYSIS_TOP_N: int = int(os.getenv("STRATEGY_ANALYSIS_TOP_N", "10"))
STRATEGY_ANALYSIS_CACHE_TTL_DAYS: int = int(os.getenv("STRATEGY_ANALYSIS_CACHE_TTL_DAYS", "7"))
STRATEGY_ANALYSIS_MAX_POSITIONS: int = int(os.getenv("STRATEGY_ANALYSIS_MAX_POSITIONS", "50"))
STRATEGY_ANALYSIS_MAX_TOKENS: int = 4096
# Regenerate rate limit per user per day (most expensive endpoint)
STRATEGY_REGEN_DAILY_LIMIT: int = int(os.getenv("STRATEGY_REGEN_DAILY_LIMIT", "5"))

# ── Claude cost tracking (Sonnet 4 pricing, per 1M tokens) ───────────────────
CLAUDE_INPUT_COST_PER_1M: float = float(os.getenv("CLAUDE_INPUT_COST_PER_1M", "3.0"))
CLAUDE_OUTPUT_COST_PER_1M: float = float(os.getenv("CLAUDE_OUTPUT_COST_PER_1M", "15.0"))

# ── Composite ranking weights ─────────────────────────────────────────────────
RANKING_WEIGHTS: dict[str, float] = {
    "total_pnl": float(os.getenv("WEIGHT_TOTAL_PNL", "0.40")),
    "realized_position_count": float(os.getenv("WEIGHT_REALIZED_POSITIONS", "0.20")),
    "pct_pnl_from_top_3_positions": float(os.getenv("WEIGHT_PCT_PNL_CONCENTRATION", "0.20")),
    "total_volume": float(os.getenv("WEIGHT_TOTAL_VOLUME", "0.10")),
    "portfolio_value": float(os.getenv("WEIGHT_PORTFOLIO_VALUE", "0.10")),
}

# ── Neon Auth (Better Auth) ───────────────────────────────────────────────────
# Set NEON_AUTH_BASE_URL to the "Auth URL" from Neon Console → Auth → Configuration.
# When set, the API requires OAuth sign-in via Google.  Leave blank for local
# development — auth is disabled and the app is open with a "local-dev" user.
NEON_AUTH_BASE_URL: str = os.getenv("NEON_AUTH_BASE_URL", "")
# 32+ char random secret used to validate sessions.  Generate with:
#   openssl rand -base64 32
NEON_AUTH_COOKIE_SECRET: str = os.getenv("NEON_AUTH_COOKIE_SECRET", "")

# ── Database ──────────────────────────────────────────────────────────────────
DATA_DIR: Path = Path(__file__).parent / "data"
try:
    DATA_DIR.mkdir(exist_ok=True)
except OSError:
    pass  # read-only filesystem (e.g. Vercel) — DATABASE_URL env var must be set

_db_url_env: str = os.getenv("DATABASE_URL", "")
if _db_url_env and (_db_url_env.startswith("postgresql://") or _db_url_env.startswith("postgres://")):
    DATABASE_URL: str = _db_url_env
else:
    # Default: local SQLite for CLI development
    DATABASE_URL = f"sqlite:///{DATA_DIR}/research.db"

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
