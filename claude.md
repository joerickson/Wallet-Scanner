# CLAUDE.md — Wallet-Scanner

## What this project is

A personal Python research tool for discovering and tracking skilled wallets on Polymarket. Read-only. Single-user. The output is a SQLite database, a dashboard the owner views privately, and optional alerts.

The dashboard MAY be deployed to a personal hosting environment (Vercel, Railway, Fly, a personal VPS, etc.) so the owner can view it from any device, including mobile. This is personal infrastructure, not a public product.

## What this project is NOT

- **Not a trading bot.** No buy/sell execution layer. No wallet connections for write operations. No private keys are ever loaded, stored, or referenced.
- **Not a multi-tenant SaaS.** No signup flow, no public users table, no per-user data isolation, no billing. The deployment, if any, serves exactly one person (the owner).
- **Not a public product.** Any deployed surface is access-controlled (basic auth, IP allowlist, Cloudflare Access, single-user token, or similar). It is not crawlable, not indexable, not advertised.
- **Not an investment recommendation engine.** It surfaces information. Decisions are the owner's.

If a task asks for any of the above, stop and ask the user before proceeding.

## Deployment guidance

The owner runs this in three possible modes:
- **Local CLI** — `python main.py scan` from the laptop. The default development workflow.
- **Personal VPS daemon** — for the alerts poller running 24/7. Single VPS, owner-controlled.
- **Personal hosted dashboard** — a small web view of the leaderboard and alerts, hosted on Vercel / Railway / Fly / equivalent, accessible only to the owner.

Hosting the dashboard is allowed and encouraged for accessibility. The dashboard MUST:
- Be access-controlled (basic auth via env vars, Cloudflare Access, Vercel password protection, or similar — no anonymous access)
- Read from the same SQLite database the scanner writes to (file storage on Vercel via a mounted volume or external storage like Turso/LibSQL is fine)
- Display only — no trade execution, no key handling, no order placement
- Have NO public signup, NO user management, NO multi-tenancy

If hosting on Vercel, the dashboard layer can be a thin FastAPI or Flask app, OR a small Next.js read-only frontend that calls a Python API — Claude should ask the owner which they prefer before adding a web layer.

## Tech stack

### Core (always)
- **Python 3.11+** for scanner, analysis, watch, and any backend
- **anthropic** SDK — model `claude-sonnet-4-20250514` for scanner qualitative review, `claude-opus-4-7` only for periodic deep analysis
- **httpx** (async) for all HTTP — never `requests`
- **tenacity** for retries with exponential backoff
- **sqlmodel** for SQLite ORM
- **pandas / numpy** for stats
- **python-dotenv** for config
- **pytest** + **pytest-asyncio** for tests

### Local terminal dashboard
- **rich + textual** — for the developer's local terminal view

### Hosted dashboard (optional, if owner chooses to deploy)
- **FastAPI** for a thin read-only API serving the SQLite data
- **Next.js + Tailwind** if the owner wants a polished mobile-friendly web view, OR plain Jinja2 templates served from FastAPI for a simpler stack
- Authentication via Vercel password protection, Cloudflare Access, or HTTP basic auth — owner picks

Do not add Postgres unless the dataset outgrows SQLite (very unlikely for single-user). Do not add Docker unless the owner asks. Do not add Redis or a job queue — async Python with `asyncio.create_task` is sufficient at this scale.

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
- Migrations append-only — add columns, never drop
- Use transactions for multi-row writes
- All DB writes go through `repository.py` modules — no inline SQL in business logic
- If hosted dashboard needs DB access, it reads from the same SQLite file (or Turso/LibSQL if deployed); never duplicates state

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
3. **No public multi-tenant features.** No signup, no public users, no billing. Single-user only.
4. **Any hosted dashboard MUST be access-controlled.** Basic auth, Cloudflare Access, Vercel password protection, or equivalent. Never publicly accessible.
5. **No fabricated metrics.** If a wallet has fewer than 90 trades, Sharpe is `None`, not estimated.
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
- Vercel password protection: https://vercel.com/docs/security/deployment-protection

## When in doubt

Ask. Scope discipline matters — but accessibility for the owner (mobile dashboard, hosted alerts) is a feature, not a violation.
