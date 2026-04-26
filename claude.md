# Wallet-Scanner / predictionscanner.io

Research tool for analyzing Polymarket wallets. Identifies skilled traders by combining Polymarket's authoritative leaderboard with per-position data from `/positions` and qualitative AI analysis. Hosted at predictionscanner.io.

## Stack

- Python 3.11+, FastAPI on Vercel serverless
- React + Vite + TypeScript frontend (in `dashboard/` — built to static assets, served by FastAPI)
- Postgres via Neon (`DATABASE_URL`), Launch tier for storage headroom
- Neon Auth (Better Auth) for authentication — opaque session tokens
- Anthropic Claude for qualitative analysis (model: `claude-sonnet-4-5-20250929` or current best Sonnet)
- Voyage AI for embeddings if/when RAG features are added (`voyage-3`)
- GitHub Actions for scheduled scans (Mondays 06:00 UTC, 5h timeout, `--incremental`)

## Architecture

**Frontend (React + Vite SPA)**
- Lives in `dashboard/`, builds to `dashboard/dist/`, served as static by FastAPI
- Talks to Neon Auth directly via `@neondatabase/auth` (or `@neondatabase/neon-js`)
- After authentication, attaches the JWT as a Bearer token on all calls to our API
- Routes: `/login`, `/` (leaderboard), `/wallet/:address`, `/watchlist`

**Backend (FastAPI on Vercel)**
- API routes under `/api/*` — leaderboard, watchlist CRUD, strategy analysis, regeneration, health
- Validates opaque session tokens by proxying to Neon Auth's `/get-session` endpoint
- No `/api/auth/*` proxy routes — the frontend talks to Neon Auth directly
- DB access via SQLModel + psycopg2-binary

**Scanner (Python CLI)**
- `python main.py scan` — discovers wallets via `/v1/leaderboard`, fetches `/positions`, computes metrics
- `python main.py analyze-strategies --top N` — runs Claude qualitative analysis on top N wallets
- Scheduled via GitHub Actions, writes directly to Neon (no API in between)
- Source of truth: `/v1/leaderboard` for P&L/volume; `/positions` for per-position resolution data
- `/activity` is NOT used — it's a transaction log without resolution data and proved insufficient

## Workflow

- Architecture and planning happen in Claude web chat
- Implementation via Claude Code on GitHub issues → PR → review → merge → Vercel auto-deploys
- Local dev in Codespaces or laptop with `.env` (gitignored) holding `DATABASE_URL`, `ANTHROPIC_API_KEY`, optionally `NEON_AUTH_BASE_URL` for testing
- The `.env.example` file documents required env vars; keep it current

## Build pipeline

- Backend: `pip install -r requirements.txt`; FastAPI runs as Vercel serverless function via `api/index.py`
- Frontend: `cd dashboard && npm install && npm run build` → produces `dashboard/dist/`
- `vercel.json` configures the Vercel build to install Python deps AND build the Vite app, then deploy `api/` as serverless and `dashboard/dist/` as static
- FastAPI serves `dashboard/dist/index.html` for `/` and any non-`/api/*` route (SPA fallback), with assets at `/assets/*`

## Database conventions

- All sessions use `expire_on_commit=False` (avoid detached-instance errors during batch upserts)
- Numpy values are coerced to native Python types before persistence — wrap with `float()` or `int()`, or use a `_to_python_number()` helper
- Position table is **append-mostly**: `first_seen_at`, `last_seen_at`, `is_active`. Don't delete; mark inactive when a position disappears from the API.
- User-scoped data references `neon_auth.user.id` (string) as foreign key
- `walletmetrics` schema is position-derived (no `win_count`/`win_rate`/`sharpe_ratio` — those required trade-level data we can't reliably get from `/activity`)

## Auth conventions

- Frontend handles all auth flows directly via the Neon Auth SDK
- Backend never proxies auth requests
- Backend validates the JWT on every protected endpoint via `Depends(get_current_user)`
- `require_auth()` is in `api/auth.py`; sends the bearer token as `__Secure-neon-auth.session_token` cookie to `{NEON_AUTH_BASE_URL}/get-session`, with a 60 s in-memory cache keyed by SHA256(token)
- `/api/health` is the only public endpoint; everything else is auth-protected

## Tone and scope

This is a real product. Build features that match what a working analyst would want. Don't preemptively constrain the design — if a feature warrants Postgres, frontend interactivity, multi-user state, third-party services, or new infrastructure, use them. Make architectural decisions based on what the product actually needs, not on a desire to keep the codebase artificially small.

## What this project is

- A research tool for the owner. Hosted publicly because it's convenient, gated by auth so it isn't browsed by strangers.
- A platform for testing whether public on-chain data on Polymarket reveals replicable strategies.
- A long-running, scheduled-scan-driven dataset that grows in value over time.

## What this project is not

- **A trading bot.** The scanner is read-only research. It never executes trades. No private keys.
- **A signal service.** We don't sell or distribute alpha. The site is for the owner's research, possibly extended to a small number of trusted collaborators.
- **Multi-tenant SaaS.** Users have their own watchlists, but there's no team/org model and no plan to add one.

## Sensitive data

- `ANTHROPIC_API_KEY` — `.env` (gitignored), GitHub repo secrets, Vercel env vars
- `DATABASE_URL` — same locations
- `NEON_AUTH_BASE_URL` — server-side only; the frontend uses `VITE_NEON_AUTH_URL` (set to the same value but with the `VITE_` prefix so Vite inlines it into the client bundle)
- Never commit `.env`. Never log API keys or DB credentials.
- `NEON_AUTH_COOKIE_SECRET` is **not used** — Better Auth cookies are signed by Neon's hosted service. Remove if present.

## Development tips

- Always run a small `--max-wallets 50` or `--top 1` test before triggering a full scan or analysis
- When debugging, prefer Vercel's runtime logs over guessing
- Schema changes should be backwards-compatible; existing data should be preserved through migrations
- Bumping the GitHub Actions timeout is fine when needed; 5 hours is a sensible ceiling
