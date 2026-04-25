# Wallet Scanner — Polymarket Research Tool

A local-only Python CLI for discovering and tracking skilled traders on Polymarket.
**Read-only research only — no trading, no execution, no private keys.**

---

## Architecture

```
Wallet-Scanner/
├── main.py              CLI entry point (8 commands)
├── config.py            All config loaded from .env
├── requirements.txt
├── .env.example
├── data/
│   ├── schema.py        SQLModel table definitions (Wallet, Trade, WalletMetrics, …)
│   └── database.py      Engine + session management
├── scanner/
│   ├── client.py        Async httpx wrapper — rate-limited, cached, retry-backed
│   ├── repository.py    All DB reads/writes for scanner module
│   ├── metrics.py       Pure-function stats: win rate, Sharpe, profit factor, …
│   ├── ranking.py       Composite ranking algorithm
│   └── scanner.py       Scan orchestrator (async, bounded concurrency)
├── analysis/
│   ├── claude_review.py Claude qualitative review — top 200 wallets only
│   ├── patterns.py      Behaviour pattern extraction
│   └── red_flags.py     Heuristic detectors: survivorship, concentration, …
├── watch/
│   ├── poller.py        Async position-polling loop
│   └── alerter.py       Terminal + Discord/Telegram alerts
├── dashboard/
│   └── app.py           4-panel Textual dashboard
└── tests/
    ├── conftest.py
    ├── test_metrics.py
    ├── test_ranking.py
    └── test_red_flags.py
```

### Data flow

```
Polymarket Data API
       │
       ▼
scanner/client.py   ← rate-limited 2 req/s, in-memory cache + TTL, tenacity retries
       │
       ▼
scanner/scanner.py  ← async gather with Semaphore(50) concurrency cap
       │
       ├─ parse_trades → Trade rows → SQLite
       ├─ compute_metrics → WalletMetrics rows
       ├─ apply_hard_filters (min 100 trades, 60% win rate, $5k volume)
       ├─ rank_wallets → WalletRanking rows
       ├─ red_flags.get_red_flags → heuristic_red_flags JSON
       └─ claude_review (top 200 only) → skill_signal, edge_hypothesis, notes
              │
              ▼
         SQLite research.db
              │
       ┌──────┴──────────────────────────┐
       ▼                                 ▼
main.py leaderboard/wallet          dashboard/app.py
main.py alerts → watch/poller.py
```

---

## Setup

### Requirements

- Python 3.11+
- An Anthropic API key (for the qualitative review step)

```bash
git clone https://github.com/joerickson/Wallet-Scanner
cd Wallet-Scanner

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

---

## CLI Commands

### Full scan

```bash
python main.py scan
```

Runs the complete pipeline: discover wallets → fetch trades → compute metrics → rank →
Claude review top 200. Expects ≤30 minutes for the full wallet universe.

```bash
python main.py scan --incremental
```

Only refreshes wallets not scanned in the last 24 hours. Safe to run daily as a cron job.

### Leaderboard

```bash
python main.py leaderboard            # Top 50 in terminal
python main.py leaderboard --top 100
python main.py leaderboard --export csv --output top100.csv
python main.py leaderboard --export json --output top100.json
```

### Single wallet deep-dive

```bash
python main.py wallet 0xabc...
```

Shows all metrics, rank, Claude review, red flags, and recent trade history.

### Watch list

```bash
python main.py watch 0xabc...         # add one wallet
python main.py watch --top 25         # add top 25 from leaderboard
python main.py watch                  # list current watchlist
```

### Live alert feed

```bash
python main.py alerts                 # poll every 5 min (default)
python main.py alerts --interval 60   # poll every 60 seconds
```

Polls watched wallets for new/closed positions and prints rich-formatted alerts.
Sends Discord/Telegram webhooks if configured in `.env`.

### Dashboard

```bash
python main.py dashboard
```

Launches a 4-panel Textual terminal dashboard:
- **Top-left**: Leaderboard (click any row to load detail)
- **Top-right**: System status
- **Bottom-left**: Recent alert feed (auto-refreshes every 60 s)
- **Bottom-right**: Selected wallet detail with metrics + Claude review

Keybindings: `R` refresh, `D` toggle dark/light, `Q` quit.

---

## Configuration reference

All settings live in `.env` (see `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `POLYMARKET_DATA_API_BASE` | `https://data-api.polymarket.com` | Override for testing |
| `API_RATE_LIMIT` | `2.0` | Requests/second cap |
| `MIN_TRADES` | `100` | Hard filter — minimum lifetime trades |
| `MIN_WIN_RATE` | `0.60` | Hard filter — minimum win rate |
| `MIN_VOLUME_USD` | `5000.0` | Hard filter — minimum USDC volume |
| `CLAUDE_REVIEW_TOP_N` | `200` | Wallets sent to Claude (never the full set) |
| `WEIGHT_WIN_RATE` | `0.30` | Composite score weight |
| `WEIGHT_SHARPE` | `0.25` | Composite score weight |
| `WEIGHT_PROFIT_FACTOR` | `0.20` | Composite score weight |
| `WEIGHT_TOTAL_PNL` | `0.15` | Composite score weight |
| `WEIGHT_TRADE_COUNT` | `0.10` | Composite score weight |
| `WALLET_CACHE_TTL` | `86400` | Seconds before a wallet is considered stale |
| `API_CACHE_TTL` | `3600` | Seconds for raw API response cache |
| `POLL_INTERVAL` | `300` | Alert polling interval (seconds) |
| `DISCORD_WEBHOOK_URL` | *(optional)* | Post alerts to Discord |
| `TELEGRAM_BOT_TOKEN` | *(optional)* | Telegram bot for alerts |
| `TELEGRAM_CHAT_ID` | *(optional)* | Telegram chat/channel ID |
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |

---

## Interpreting results

### Composite score (0–1)

A weighted combination of five normalised components:

| Component | Default weight | What it measures |
|---|---|---|
| Win rate | 30% | Fraction of completed trades that were profitable |
| Sharpe ratio | 25% | Risk-adjusted return consistency (`None` if <90 trades) |
| Profit factor | 20% | Gross profit ÷ gross loss |
| Total P&L | 15% | Absolute profit (log-normalised up to $100k) |
| Trade count | 10% | Volume of evidence |

Higher scores mean more evidence of consistent, risk-adjusted skill. The weights are configurable.

### Skill signal (Claude, 0–1)

Claude's qualitative assessment of whether the statistical profile is likely due to:
- **0.0–0.3**: Noise, survivorship, or data artefact
- **0.3–0.6**: Possible edge, ambiguous
- **0.6–0.8**: Likely genuine edge with identifiable pattern
- **0.8–1.0**: Strong, repeatable skill signal

### Red flags

| Flag | What it means |
|---|---|
| `single_bet_dominance` | >50% of all trades in one market — luck rather than diversified skill |
| `market_concentration` | Fewer than 3 distinct markets despite enough trades |
| `survivorship_bias` | >90% win rate on <200 trades — small sample, cherry-picked history |
| `volume_size_mismatch` | P&L > 300% of volume — likely data artefact |
| `recency_cliff` | Recent win rate < 70% of historical — may be mean-reverting |
| `insider_timing` | Consistent entries within 1 hour of market open |

A wallet with red flags is **not automatically disqualified** — it means you should scrutinise it more carefully before acting on it.

### Sharpe ratio (None vs number)

By design, Sharpe is `None` for any wallet with fewer than 90 completed trades.
This is a hard rule (not a soft default) — estimating Sharpe from 30 trades produces
misleading numbers. `None` means "insufficient data", not zero.

---

## Cost estimation

Claude qualitative review is called only on the top 200 wallets after numerical filtering.

| Model | Calls | Estimated cost |
|---|---|---|
| `claude-sonnet-4-20250514` | 200 | ~$0.50–1.50 |

A full weekly refresh should cost well under $4 in Claude API usage.

---

## Realistic expectations

The Polymarket Data API leaderboard caps each slice at offset=1000 with a maximum of 50 results per page. To build the widest possible wallet universe, the scanner sweeps multiple combinations of `timePeriod` (ALL, MONTH, WEEK), `category` (OVERALL, POLITICS, SPORTS, CRYPTO, ECONOMICS, TECH, FINANCE), and `orderBy` (PNL, VOL), then deduplicates by `proxyWallet`.

**Realistic wallet universe: 2,000–4,000 unique addresses** after full deduplication across all sweep dimensions.

Filtering this down to the top ~50 wallets with consistent, risk-adjusted skill is still meaningful at this scale. Early claims of 14,000+ addressable wallets were unverifiable and not reproducible via the official API.

---

## Automated scans

The repository includes a GitHub Actions workflow (`.github/workflows/scheduled-scan.yml`) that
runs the wallet scanner automatically every **Monday at 06:00 UTC** and writes results to Turso,
so the leaderboard stays fresh without needing to open Codespaces.

### Changing the schedule

Edit the `cron` line in `.github/workflows/scheduled-scan.yml`:

```yaml
on:
  schedule:
    - cron: "0 6 * * 1"   # ← change this
```

Use [crontab.guru](https://crontab.guru) to build a cron expression. Examples:

| Schedule | Expression |
|---|---|
| Every day at midnight UTC | `0 0 * * *` |
| Every 6 hours | `0 */6 * * *` |
| Weekdays at 07:00 UTC | `0 7 * * 1-5` |

### Manual trigger

Go to **Actions → Scheduled Wallet Scan → Run workflow** to kick off a scan immediately
without waiting for the next cron tick.

### Required GitHub secrets

Add these under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (`sk-ant-...`) |
| `TURSO_DATABASE_URL` | Your Turso database URL (`libsql://yourdb.turso.io`) |
| `TURSO_AUTH_TOKEN` | Turso auth token for the database |

To create a Turso database: `turso db create wallet-scanner` then `turso db show wallet-scanner`.

### Cost expectations

| Cost | Estimate |
|---|---|
| GitHub Actions runtime per scan | ~30 min (free tier: 2,000 min/month — ~66 scans) |
| Anthropic API per scan | ~$2–4 (Claude called on top 200 wallets only) |

Actions runtime is free; Claude API calls are not. The `--incremental` flag skips wallets
refreshed in the last 24 hours and skips Claude reviews completed in the last 7 days,
keeping per-run API costs low for weekly cadence.

---

## Running tests

```bash
pytest tests/ -v
```

The test suite covers `scanner/metrics.py`, `scanner/ranking.py`, and `analysis/red_flags.py`
with fixtures and does not make any live API calls.

---

## Hard limits (by design)

- **No trading.** No buy/sell execution. No wallet connections for write operations.
- **No private keys.** The project never loads, stores, or transmits any keys.
- **No web framework.** Terminal only.
- **No public deployment.** Local laptop or personal VPS only.
- **No fabricated metrics.** Sharpe is `None`, not estimated, when data is insufficient.
- **Claude called on top 200 only.** Never on the full wallet population.
