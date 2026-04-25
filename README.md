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
│   ├── schema.py        SQLModel table definitions (Wallet, Position, WalletMetrics, …)
│   └── database.py      Engine + session management
├── scanner/
│   ├── client.py        Async httpx wrapper — rate-limited, cached, retry-backed
│   ├── repository.py    All DB reads/writes for scanner module
│   ├── metrics.py       Pure-function stats from positions + leaderboard data
│   ├── ranking.py       Composite ranking algorithm
│   └── scanner.py       Scan orchestrator (async, bounded concurrency)
├── analysis/
│   ├── claude_review.py Claude qualitative review — top 200 wallets only
│   ├── patterns.py      Behaviour pattern stubs (position-based analysis)
│   └── red_flags.py     Heuristic detectors: survivorship, concentration, …
├── watch/
│   ├── poller.py        Async position-polling loop
│   └── alerter.py       Terminal + Discord/Telegram alerts
├── dashboard/
│   └── app.py           4-panel Textual dashboard
├── scripts/
│   └── drop_trade_table.py  Migration: drop old trade table, recreate walletmetrics
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
/v1/leaderboard sweep  ← wallet discovery + authoritative P&L/volume per wallet
       │
       ▼
scanner/client.py   ← rate-limited 2 req/s, in-memory cache + TTL, tenacity retries
       │
       ▼
scanner/scanner.py  ← async gather with Semaphore(50) concurrency cap
       │
       ├─ /positions per wallet → Position rows → DB
       ├─ /value per wallet → portfolio_value
       ├─ compute_metrics → WalletMetrics rows
       ├─ apply_hard_filters (min 30 positions, $5k P&L, $5k volume, 10 resolved)
       ├─ rank_wallets → WalletRanking rows
       ├─ red_flags.get_red_flags → heuristic_red_flags JSON
       └─ claude_review (top 200 only) → skill_signal, edge_hypothesis, notes
              │
              ▼
  SQLite (local) or Neon Postgres (hosted)
              │
       ┌──────┴──────────────────────────┐
       ▼                                 ▼
main.py leaderboard/wallet          dashboard/app.py
main.py alerts → watch/poller.py
```

### Why leaderboard + positions, not /activity

`/activity` returns transaction events without resolution data — all 3.4M historical
trade rows had `pnl=NULL` and `is_resolved=FALSE`. Polymarket's `/v1/leaderboard`
provides authoritative P&L computed by Polymarket itself. `/positions` provides
per-position resolution data including `redeemable` (TRUE = market resolved),
`cashPnl`, and position sizing. The scanner uses both.

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

### Database migration (existing Neon deployments)

If upgrading from the trade-based schema, run once after deploying this version:

```bash
python scripts/drop_trade_table.py
```

This drops the `trade` table (3.4M rows, ~400MB) and recreates `walletmetrics`
with the new position-based schema. Then run a full scan to repopulate.

---

## CLI Commands

### Full scan

```bash
python main.py scan
```

Runs the complete pipeline: sweep leaderboard → fetch positions → compute metrics →
rank → Claude review top 200. Expects ≤30 minutes for the full wallet universe.

```bash
python main.py scan --incremental
```

Re-sweeps leaderboard for fresh pnl/vol, then only refreshes wallets not scanned
in the last 24 hours. Safe to run daily as a cron job.

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

Shows total P&L, position metrics, top 10 positions by absolute P&L, red flags,
and Claude review.

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
| `DATABASE_URL` | *(optional)* | Neon Postgres connection string; omit to use local SQLite |
| `POLYMARKET_DATA_API_BASE` | `https://data-api.polymarket.com` | Override for testing |
| `API_RATE_LIMIT` | `2.0` | Requests/second cap |
| `MIN_TRADES` | `30` | Hard filter — minimum position count |
| `MIN_PNL` | `5000.0` | Hard filter — minimum P&L from leaderboard |
| `MIN_VOLUME_USD` | `5000.0` | Hard filter — minimum USDC volume |
| `MIN_REALIZED_POSITIONS` | `10` | Hard filter — minimum resolved positions |
| `CLAUDE_REVIEW_TOP_N` | `200` | Wallets sent to Claude (never the full set) |
| `WEIGHT_TOTAL_PNL` | `0.40` | Composite score weight |
| `WEIGHT_REALIZED_POSITIONS` | `0.20` | Composite score weight |
| `WEIGHT_PCT_PNL_CONCENTRATION` | `0.20` | Composite score weight (inverse — lower is better) |
| `WEIGHT_TOTAL_VOLUME` | `0.10` | Composite score weight |
| `WEIGHT_PORTFOLIO_VALUE` | `0.10` | Composite score weight |
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
| Total P&L | 40% | Absolute profit from leaderboard (log-normalised up to $500k) |
| Realized position count | 20% | Number of resolved positions — more = more skill evidence |
| P&L concentration (inverse) | 20% | Lower top-3 P&L concentration = better diversification |
| Total volume | 10% | Trading volume (log-normalised up to $1M) |
| Portfolio value | 10% | Still active = current skin in the game |

Higher scores mean more evidence of consistent, diversified skill across many resolved markets.

### Skill signal (Claude, 0–1)

Claude's qualitative assessment of whether the statistical profile is likely due to:
- **0.0–0.3**: Noise, survivorship, or data artefact
- **0.3–0.6**: Possible edge, ambiguous
- **0.6–0.8**: Likely genuine edge with identifiable pattern
- **0.8–1.0**: Strong, repeatable skill signal

### Red flags

| Flag | What it means |
|---|---|
| `single_bet_dominance` | Top 3 positions account for >70% of P&L — luck rather than diversified skill |
| `market_concentration` | >70% of positions in a single market |
| `survivorship` | Unresolved positions outnumber resolved by 3:1 — wallet looks good only because losing bets haven't settled |
| `recency_cliff` | Recent win rate < 70% of historical (requires external data, not auto-detected) |

A wallet with red flags is **not automatically disqualified** — it means you should scrutinise it more carefully before acting on it.

---

## Cost estimation

Claude qualitative review is called only on the top 200 wallets after numerical filtering.

| Model | Calls | Estimated cost |
|---|---|---|
| `claude-sonnet-4-20250514` | 200 | ~$0.50–1.50 |

A full weekly refresh should cost well under $4 in Claude API usage.

---

## Realistic expectations

The Polymarket Data API leaderboard caps each slice at offset=1000 with a maximum of 50 results per page. The scanner sweeps multiple combinations of `timePeriod` (ALL, MONTH, WEEK), `category` (OVERALL, POLITICS, SPORTS, CRYPTO, ECONOMICS, TECH, FINANCE), and `orderBy` (PNL, VOL), then deduplicates by `proxyWallet`.

**Realistic wallet universe: 2,000–4,000 unique addresses** after full deduplication.

Of these, the hard filters (min $5k P&L, min $5k volume, min 30 positions, min 10 resolved) should pass **1,000–2,000 wallets**. After red flag review and Claude qualitative analysis, the analytically interesting universe is typically **50–200 wallets**.

---

## Automated scans

The repository includes a GitHub Actions workflow (`.github/workflows/scheduled-scan.yml`) that
runs the wallet scanner automatically every **Monday at 06:00 UTC** and writes results to Neon
Postgres, so the leaderboard stays fresh without needing to open Codespaces.

### Manual trigger

Go to **Actions → Scheduled Wallet Scan → Run workflow** to kick off a scan immediately.

### Required GitHub secrets

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (`sk-ant-...`) |
| `DATABASE_URL` | Your Neon Postgres connection string |

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
- **No fabricated metrics.** All metrics derive from real API data.
- **Claude called on top 200 only.** Never on the full wallet population.
