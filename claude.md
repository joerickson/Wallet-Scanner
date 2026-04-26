# CLAUDE.md — Wallet-Scanner

## What this project is

A personal Python research tool for discovering and tracking skilled wallets on Polymarket. Read-only. Single-user. The output is a Postgres database (Neon), a dashboard the owner views privately, and optional alerts.

### Architectural decision (2026-04)

`/activity` proved insufficient as the primary data source — it is a transaction log without resolution data. The scanner was refactored to use:

- **`/v1/leaderboard`**: Provides authoritative P&L and volume per wallet, already computed by Polymarket.
- **`/positions`**: Provides per-position resolution data including `redeemable` (TRUE = market resolved), `cashPnl`, `realizedPnl`, and position sizing.
- **`/value`**: Returns current portfolio USDC value.

The `trade` table was dropped. The `position` table replaces it. `WalletMetrics` now stores leaderboard-derived pnl/vol plus position-based metrics.

Realistic universe: top 1,000–2,000 wallets that pass hard filters, of which 50–200 will be analytically interesting after red flag review.

The dashboard MAY be deployed to a personal hosting environment (Vercel, Railway, Fly, a personal VPS, etc.) so the owner can view it from any device, including mobile. This is personal infrastructure, not a public product.

## What this project is NOT

- **Not a trading bot.** No buy/sell execution layer. No wallet connections for write operations. No private keys are ever loaded, stored, or referenced.
- **Not a multi-tenant SaaS.** No signup flow, no public users table, no per-user data isolation, no billing. The deployment, if any, serves exactly one person (the owner).
- **Not an investment recommendation engine.** It surfaces information. Decisions are the owner's.

If a task asks for any of the above, stop and ask the owner before proceeding.

## Deployment guidance

The owner runs this in three possible modes:

- **Local CLI** — `python main.py scan` from the laptop. The default development workflow.
- **Personal VPS daemon** — for the alerts poller running 24/7. Single VPS, owner-controlled.
- **Personal hosted dashboard** — a small web view of the leaderboard and alerts, hosted on Vercel / Railway / Fly / equivalent.

Access control on the hosted dashboard is the owner's call, not a project rule. Options range from "none — the URL is the secret" to HTTP basic auth to Cloudflare Access to Vercel's built-in protection. All are acceptable. The owner decides based on their threat model.

The dashboard MUST display only — no trade execution, no key handling, no order placement. How access is gated, if at all, is owner discretion.

If hosting on Vercel, the dashboard layer can be a thin FastAPI or Flask app, OR a small Next.js read-only frontend that calls a Python API — Claude should ask the owner which they prefer before adding a web layer.

## Tech stack

### Core (always)
- **Python 3.11+** for scanner, analysis, watch, and any backend
- **anthropic** SDK — model `claude-sonnet-4-20250514` for scanner qualitative review, `claude-opus-4-7` only for periodic deep analysis
- **httpx** (async) for all HTTP — never `requests`
- **tenacity** for retries with exponential backoff
- **sqlmodel** + **sqlalchemy** for ORM; **psycopg[binary]** for Postgres driver
- **pandas / numpy** for stats
- **python-dotenv** for config
- **pytest** + **pytest-asyncio** for tests

### Local terminal dashboard
- **rich + textual** — for the developer's local terminal view

### Hosted dashboard (optional, if owner chooses to deploy)
- **FastAPI** for a thin read-only API serving the SQLite data
- **Next.js + Tailwind** if the owner wants a polished mobile-friendly web view, OR plain Jinja2 templates served from FastAPI for a simpler stack
- Authentication is optional and is the owner's call

Use Postgres (via `DATABASE_URL`) for hosted deployments (Vercel, GitHub Actions). SQLite remains the default for local CLI development. The libSQL/Turso path was abandoned due to driver compatibility issues with SQLAlchemy (`sqlite3.Connection has no create_function attribute`). Do not add Docker unless the owner asks. Do not add Redis or a job queue — async Python with `asyncio.create_task` is sufficient at this scale.

## Folder structure (canonical)

```
Wallet-Scanner/
├── main.py                    # CLI entry, click or argparse
├── config.py                  # All config + .env loading
├── requirements.txt
├── .env.example
├── README.md
├── CLAUDE.md
├── data/                      # SQLite DBs, gitignored except .gitkeep
├── scanner/                   # Wallet discovery + metrics
├── analysis/                  # Claude review + pattern extraction + red flags
├── watch/                     # Polling + alerting
├── dashboard/
│   ├── terminal/              # rich/textual local TUI
│   └── web/                   # OPTIONAL — hosted read-only dashboard (only if owner adds it)
├── tests/                     # pytest suite, mirrors module structure
└── vercel.json                # OPTIONAL — only if hosted dashboard is deployed
```

When adding new functionality, prefer extending an existing module over creating a new one. New top-level folders require justification.

## Conventions

### Python style
- Type hints on every function signature
- `from __future__ import annotations` at the top of every module
- `dataclasses` or `pydantic` BaseModel for structured data, never bare dicts for domain objects
- Async by default for I/O. Sync for pure CPU work (stats, ranking).
- Format with `ruff format`. Lint with `ruff check`.

### Imports
- stdlib → third-party → local, separated by blank lines
- Absolute imports inside the project, no relative
- No wildcard imports

### Error handling
- Network calls use `tenacity` retry with backoff: 3 retries, exponential, max 30s
- Catch specific exceptions, never bare `except:`
- Log with context (which wallet, which market, what request)
- The scanner must never crash on a single bad wallet. Skip + log + continue.

### Database
- All schema in `data/schema.py` using sqlmodel
- Migrations: run `python scripts/drop_trade_table.py` when upgrading from the old trade-based schema (drops `trade` and `walletmetrics`, recreates `walletmetrics` with new fields)
- Use transactions for multi-row writes
- All DB writes go through `repository.py` modules — no inline SQL in business logic
- Local development uses SQLite at `data/research.db` (no env var needed)
- Hosted deployments use Neon Postgres — set `DATABASE_URL=postgresql://...` in env
- If hosted dashboard needs DB access, it connects via the same `DATABASE_URL`; never duplicates state

### Claude API usage
- Always use the official `anthropic` SDK
- Model strings as constants in `config.py`
- Sonnet for high-volume scanning (~200 calls per leaderboard refresh)
- Opus only for periodic deep analysis (weekly pattern report) — never for scanner
- Always include `max_tokens` explicitly
- Structured outputs: prompt for JSON, parse with pydantic, validate. Log raw on parse fail, skip — don't crash.

### Cost discipline
- Every Claude call must be justified
- Scanner calls Claude only on top 200 wallets after numerical filtering — never on the full 10k+ pool
- Estimated full-run cost: ~$2-4. If a change pushes that over $20, flag it in the PR description.

### Logging
- `logging` module, not `print`. Configured once in `config.py`.
- Levels: DEBUG / INFO / WARNING / ERROR
- Never log API keys

### Testing
- pytest with pytest-asyncio
- One test file per module
- Pure-function tests (stats, metrics, ranking) get full coverage
- Network-dependent tests use vcrpy or saved fixtures, never live API calls in CI
- Test suite must complete in under 30 seconds

### Git + commits
- Branch per feature, no direct commits to main
- Conventional commit prefix: `feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`
- One logical change per commit
- PR description: what changed, why, how it was tested, cost implications

## Hard rules

1. **No trading execution layer.** Not now, not later. Push back if asked.
2. **No private key handling.** The project never loads, stores, or transmits keys.
3. **No public multi-tenancy.** Single-user only — no signup, no users table, no billing.
4. **Access control on the deployed dashboard is owner discretion.** Not a project mandate. Implement what the owner asks for, nothing more, nothing less.
5. **No fabricated metrics.** All metrics derive from real API data. Never estimate or impute values for positions with missing resolution data.
6. **Never call Claude on the full wallet population.** Numerical filters first, Claude on top 200 only.
7. **Never silently overwrite cached data.** Cache writes include timestamps; reads check freshness.
8. **Polymarket Data API is rate-limited.** Default 2 req/sec. Faster requires explicit owner approval.

## Reference docs

- Polymarket Data API: https://docs.polymarket.com/developers/dev-resources/main
- Polymarket CLOB API (read-only endpoints only): https://docs.polymarket.com/developers/CLOB/introduction
- Anthropic SDK: https://docs.claude.com/en/api/overview
- sqlmodel: https://sqlmodel.tiangolo.com/
- textual: https://textual.textualize.io/
- FastAPI: https://fastapi.tiangolo.com/

## Auth (Neon Auth — Better Auth backend)

The hosted dashboard uses Google OAuth via **Neon Auth**, which is powered by Better Auth.
**Do NOT use legacy Stack Auth** (`@stackframe/stack`, `api.stack-auth.com`, `STACK_*` vars) — those are not configured for this project and will 404.

### How it works

1. User visits `/` → FastAPI checks the `better-auth.session_token` cookie → if missing/invalid, redirects to `/login`.
2. `/login` page shows a "Sign in with Google" button (link to `/api/auth/login?provider=google`).
3. `api/auth.py` calls `POST {NEON_AUTH_BASE_URL}/api/auth/sign-in/social` to get the Google OAuth redirect URL.
4. After Google OAuth, Neon Auth handles the callback and redirects the browser to `/api/auth/callback` on our app.
5. FastAPI validates the session by forwarding the `better-auth.session_token` cookie to `GET {NEON_AUTH_BASE_URL}/api/auth/get-session`.
6. Every protected endpoint calls `validate_session()` which hits the Neon Auth session endpoint.

### Required Vercel env vars

| Variable | Where to find |
|---|---|
| `NEON_AUTH_BASE_URL` | Neon Console → Auth → Configuration → Auth URL |
| `NEON_AUTH_COOKIE_SECRET` | Generate with `openssl rand -base64 32` |

### Local development

Leave both `NEON_AUTH_*` vars unset. `AUTH_ENABLED` in `api/auth.py` will be `False`, and every request is treated as a synthetic `local-dev` user. No OAuth flow is triggered.

### OAuth providers currently enabled

- **Email** — default
- **Google** — via Neon Auth shared keys (default)
- **GitHub** — NOT yet enabled. To add it: Neon Console → Auth → Configuration → OAuth providers.

### Protected routes

All `/api/*` routes except `/api/health` require a valid session. The GitHub Actions scheduler writes directly to Postgres via `DATABASE_URL` — it never touches the API, so it is unaffected by auth.

### User-scoped data

User identity comes from the `id` field returned by Neon Auth's get-session endpoint (a string UUID from Better Auth, stable per provider account). This is stored as `user_id` in the `user_watchlist` table. User records are managed by Neon Auth in the `neon_auth.user` schema; we store only the `id` reference.

### Post-deploy checklist

After deploying to Vercel:
1. Add `NEON_AUTH_BASE_URL` and `NEON_AUTH_COOKIE_SECRET` to Vercel env vars.
2. Add `predictionscanner.io` to trusted domains: Neon Console → Auth → Configuration → Domains.
3. Redeploy and test the Google sign-in flow.

## Scheduled scans

Weekly scans run automatically via `.github/workflows/scheduled-scan.yml` (every Monday at
06:00 UTC, plus on-demand via `workflow_dispatch`). The workflow runs:

```bash
python main.py scan --incremental
```

with `ANTHROPIC_API_KEY` and `DATABASE_URL` injected as GitHub secrets. Results are written
directly to Neon Postgres via the standard SQLAlchemy engine in `data/database.py`.

**Rules that must be preserved in any future scanner changes:**

1. **`--incremental` flag must always be accepted by `python main.py scan`.**
   - Skips wallets refreshed in the last 24 hours (controlled by `WALLET_CACHE_TTL`)
   - Skips Claude qualitative review for wallets reviewed in the last 7 days
   - Still re-ranks all wallets and writes the full leaderboard on every run
   - On first run with an empty DB, falls back to full wallet discovery automatically

2. **Cost discipline on the scheduled path.** The `--incremental` flag is what keeps the
   scheduled run from calling Claude on the full top-200 list every week. If you change the
   Claude review logic, ensure freshness skipping still works.

## When in doubt

Ask the owner. Scope discipline matters, but the owner decides what's in scope. When the owner makes a call that contradicts a previous instruction here, follow the owner — and update this file accordingly.
