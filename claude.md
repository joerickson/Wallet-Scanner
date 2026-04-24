# CLAUDE.md — Wallet-Scanner

## What this project is

A local-only Python research tool for discovering and tracking skilled wallets on Polymarket. Read-only. No trading, no execution, no private keys, no web app, no deployment.

Single-user. Runs on the developer's laptop or a small personal VPS. Output is a SQLite database, a terminal dashboard, and optional webhook alerts.

## What this project is NOT

- **Not a trading bot.** No buy/sell execution layer. No wallet connections for write operations. No private keys are ever loaded, stored, or referenced.
- **Not a SaaS.** No multi-tenant, no authentication, no users table, no FastAPI, no Flask, no Next.js, no React.
- **Not deployed to the cloud.** Runs locally via `python main.py`. May eventually run on a personal VPS for the alerts daemon — never on a public web surface.
- **Not an investment recommendation engine.** It surfaces information. Decisions are the user's.

If a task asks for any of the above, stop and ask the user before proceeding. The project's value depends on this scope discipline.

## Tech stack (locked)

- **Python 3.11+** (use modern syntax: `match` statements, `|` union types, `tomllib`)
- **anthropic** SDK — model `claude-sonnet-4-20250514` for scanner qualitative review, `claude-opus-4-7` only for periodic deep analysis
- **httpx** (async) for all HTTP — never `requests`
- **tenacity** for retries with exponential backoff
- **sqlmodel** for SQLite ORM (sits on top of SQLAlchemy + Pydantic)
- **pandas / numpy** for stats
- **rich + textual** for the terminal dashboard — never Streamlit, never Gradio
- **python-dotenv** for config
- **pytest** + **pytest-asyncio** for tests

Do not add a web framework. Do not add a frontend. Do not add Docker unless the user explicitly asks. Do not add Postgres — SQLite is the right choice for single-user local data.

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
├── dashboard/                 # rich/textual terminal app
└── tests/                     # pytest suite, mirrors module structure
```

When adding new functionality, prefer extending an existing module over creating a new one. New top-level folders require justification.

## Conventions

### Python style
- Type hints on every function signature, no exceptions
- `from __future__ import annotations` at the top of every module
- Use `dataclasses` or `pydantic` BaseModel for structured data, never bare dicts for domain objects
- Async by default for any I/O. Sync code is acceptable only for pure CPU work (stats, ranking)
- Format with `ruff format`. Lint with `ruff check`. No exceptions.

### Imports
- Standard library first, third-party second, local third — separated by blank lines
- Absolute imports only inside the project (`from scanner.metrics import compute_sharpe`), never relative
- No wildcard imports

### Error handling
- Network calls use `tenacity` retry with backoff: 3 retries, exponential, max 30s
- Catch specific exceptions, never bare `except:` or `except Exception:` without re-raising
- Log errors with context (which wallet, which market, what request) — use `logging` module, not `print`
- The scanner must never crash on a single bad wallet. Skip + log + continue.

### Database
- All schema in `data/schema.py` using sqlmodel
- Migrations are append-only — add columns, never drop. SQLite is forgiving but discipline matters.
- Use transactions for any multi-row write
- All DB writes go through a function in the relevant module's `repository.py` — never inline SQL in business logic

### Claude API usage
- Always use the official `anthropic` SDK, never raw HTTP
- Model strings as constants in `config.py` — never hardcoded in business logic
- Sonnet (`claude-sonnet-4-20250514`) for high-volume scanning (~200 calls per leaderboard refresh)
- Opus (`claude-opus-4-7`) only for periodic deep analysis (e.g., weekly pattern report) — never for scanner
- Always include `max_tokens` explicitly, never rely on defaults
- Structured outputs: prompt for JSON, parse with pydantic, validate. If parse fails, log the raw response and skip that item — don't crash.

### Cost discipline
- Every Claude call must be justified. The scanner is designed to call Claude only on the top 200 wallets after numerical filtering — never on the full 10k+ candidate pool.
- Estimated full-run cost is ~$2-4. If a change would push that over $20, flag it in the PR description.

### Logging
- Use `logging` module, not `print`. Configure once in `config.py`.
- Log levels: DEBUG for trace, INFO for normal operation, WARNING for retries and skips, ERROR for failures that affect output
- Never log private keys, API keys, or wallet seeds. The project doesn't have any of these — but if a task ever proposes adding one, refuse and ask the user.

### Testing
- pytest with pytest-asyncio for async tests
- One test file per module, mirroring structure
- Pure-function tests preferred (stats, metrics, ranking) — these should have full coverage
- Network-dependent tests use vcrpy or saved fixtures, never live API calls in CI
- A test suite run must complete in under 30 seconds

### Git + commits
- Branch per feature, no direct commits to main
- Conventional commit prefix: `feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`
- One logical change per commit
- PR description must include: what changed, why, how it was tested, and any cost implications

## Hard rules

1. **No trading execution layer.** Not now, not later. If the user asks, push back: "this should be a separate project."
2. **No private key handling.** The project doesn't load, store, reference, or transmit any keys.
3. **No web framework.** Terminal UI only.
4. **No deployment to public URLs.** Local-only. A personal VPS for the alerts daemon is the maximum.
5. **No fabricated metrics.** If a wallet has fewer than 90 trades, Sharpe is `None`, not estimated. If exit quality data is missing, the field is `None`, not zero.
6. **Never call Claude on the full wallet population.** Numerical filters first, Claude on top 200 only.
7. **Never silently overwrite cached data.** Cache writes always include a timestamp; reads always check freshness.
8. **Polymarket Data API is rate-limited.** Default to 2 req/sec. If the user wants faster, they must say so explicitly.

## Reference docs

- Polymarket Data API: https://docs.polymarket.com/developers/dev-resources/main
- Polymarket CLOB API: https://docs.polymarket.com/developers/CLOB/introduction (read-only endpoints only — order placement is out of scope)
- Anthropic SDK: https://docs.claude.com/en/api/overview
- sqlmodel: https://sqlmodel.tiangolo.com/
- textual: https://textual.textualize.io/

## When in doubt

Ask. This project's value is in being small, focused, and trustworthy. Adding scope is the failure mode, not the feature.
