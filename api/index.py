from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.auth import AUTH_ENABLED, handle_callback, require_auth, signout_response, start_email_signin, start_email_signup, start_oauth, validate_session
from config import STRATEGY_REGEN_DAILY_LIMIT
from data.database import init_db
from scanner import repository as repo

app = FastAPI(title="Wallet Scanner", docs_url=None, redoc_url=None)

_DASHBOARD_HTML = pathlib.Path(__file__).parent.parent / "dashboard" / "index.html"
_LOGIN_HTML = pathlib.Path(__file__).parent.parent / "dashboard" / "login.html"

try:
    init_db()
except Exception:
    pass


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = await validate_session(request)
    if not user and AUTH_ENABLED:
        return RedirectResponse("/login", status_code=302)
    try:
        return _DASHBOARD_HTML.read_text()
    except FileNotFoundError:
        return HTMLResponse("<p>Dashboard not found.</p>", status_code=404)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await validate_session(request)
    if user and AUTH_ENABLED:
        return RedirectResponse("/", status_code=302)
    try:
        return _LOGIN_HTML.read_text()
    except FileNotFoundError:
        return HTMLResponse("<p>Login page not found.</p>", status_code=404)


@app.get("/api/auth/login")
async def auth_login(request: Request, provider: str = "google"):
    if provider not in ("google",):
        raise HTTPException(status_code=400, detail="Provider must be 'google'")
    return await start_oauth(request, provider)


@app.post("/api/auth/login/email")
async def auth_login_email(request: Request):
    form_data = await request.form()
    action = str(form_data.get("action") or "signin")
    email = (str(form_data.get("email") or "")).strip()
    password = str(form_data.get("password") or "")
    if not email or not password:
        return RedirectResponse("/login?error=missing_credentials", status_code=302)
    if action == "signup":
        name = (str(form_data.get("name") or "")).strip()
        return await start_email_signup(request, email, password, name)
    return await start_email_signin(request, email, password)


@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    return await handle_callback(request)


@app.get("/api/auth/signout")
async def auth_signout(request: Request):
    return await signout_response(request)


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(require_auth)) -> dict:
    return {
        "id": user.get("id"),
        "email": user.get("primary_email"),
        "name": user.get("display_name"),
    }


@app.get("/api/health")
def health() -> dict:
    try:
        total = repo.get_rankings_count()
        rankings = repo.get_top_rankings(limit=1)
        last_scan_at = rankings[0].ranked_at.isoformat() if rankings else None
    except Exception:
        total = 0
        last_scan_at = None

    try:
        claude_usage = repo.get_monthly_claude_usage()
    except Exception:
        claude_usage = None

    return {
        "status": "ok",
        "ranked_wallet_count": total,
        "last_scan_at": last_scan_at,
        "claude_usage_this_month": claude_usage,
    }


@app.get("/api/leaderboard")
async def leaderboard(limit: int = 50, user: dict = Depends(require_auth)) -> dict:
    watched = repo.get_watched_addresses_for_user(user["id"])
    activity_counts = repo.get_activity_counts_for_user(user["id"])
    rows = repo.get_top_rankings(limit=min(limit, 200))
    total = repo.get_rankings_count()
    max_ranked_at = max((r.ranked_at for r in rows), default=None)

    wallets = []
    for r in rows:
        metrics = repo.get_metrics_for_wallet(r.wallet_address)

        heuristic_flags: list[str] = []
        if r.heuristic_red_flags:
            try:
                heuristic_flags = json.loads(r.heuristic_red_flags)
            except json.JSONDecodeError:
                pass

        claude_flags: list[str] = []
        if r.claude_red_flags:
            try:
                claude_flags = json.loads(r.claude_red_flags)
            except json.JSONDecodeError:
                pass

        wallets.append({
            "address": r.wallet_address,
            "rank": r.rank,
            "composite_score": r.composite_score,
            "skill_signal": r.skill_signal,
            "edge_hypothesis": r.edge_hypothesis,
            "claude_notes": r.claude_notes,
            "ranked_at": r.ranked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "heuristic_red_flags": heuristic_flags,
            "claude_red_flags": claude_flags,
            "red_flags": heuristic_flags + claude_flags,
            "is_watched": r.wallet_address in watched,
            "new_activity_count": activity_counts.get(r.wallet_address, 0),
            "metrics": {
                "trade_count": metrics.trade_count,
                "total_pnl": metrics.total_pnl,
                "total_volume": metrics.total_volume,
                "portfolio_value": metrics.portfolio_value,
                "realized_position_count": metrics.realized_position_count,
                "unresolved_position_count": metrics.unresolved_position_count,
                "market_count": metrics.market_count,
                "pct_pnl_from_top_3_positions": metrics.pct_pnl_from_top_3_positions,
            } if metrics else None,
        })

    return {
        "meta": {
            "total": total,
            "showing": len(wallets),
            "last_ranked_at": max_ranked_at.strftime("%Y-%m-%dT%H:%M:%SZ") if max_ranked_at else None,
        },
        "wallets": wallets,
    }


@app.get("/api/watchlist")
async def get_watchlist(user: dict = Depends(require_auth)) -> list[dict]:
    entries = repo.get_user_watchlist(user["id"])
    return [
        {
            "wallet_address": e.wallet_address,
            "added_at": e.added_at.isoformat(),
            "notes": e.notes,
        }
        for e in entries
    ]


@app.post("/api/watchlist")
async def add_watchlist(request: Request, user: dict = Depends(require_auth)) -> dict:
    body = await request.json()
    wallet_address = (body.get("wallet_address") or "").strip()
    if not wallet_address:
        raise HTTPException(status_code=400, detail="wallet_address is required")
    added = repo.add_user_watchlist_entry(user["id"], wallet_address)
    return {"added": added, "wallet_address": wallet_address}


@app.delete("/api/watchlist/{address}")
async def remove_watchlist(address: str, user: dict = Depends(require_auth)) -> dict:
    removed = repo.remove_user_watchlist_entry(user["id"], address)
    if not removed:
        raise HTTPException(status_code=404, detail="Entry not found in watchlist")
    return {"removed": True, "wallet_address": address}


@app.get("/api/watchlist/summary")
async def watchlist_summary(user: dict = Depends(require_auth)) -> dict:
    entries = repo.get_user_watchlist(user["id"])
    activity = repo.get_activity_counts_for_user(user["id"])
    watched_count = len(entries)
    wallets_with_new = sum(1 for count in activity.values() if count > 0)
    total_new = sum(activity.values())
    return {
        "watched_count": watched_count,
        "wallets_with_new_activity": wallets_with_new,
        "total_new_positions": total_new,
    }


@app.post("/api/watchlist/{address}/seen")
async def mark_wallet_seen(address: str, user: dict = Depends(require_auth)) -> dict:
    updated = repo.update_watchlist_last_seen(user["id"], address)
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found in watchlist")
    return {"updated": True, "wallet_address": address}


@app.get("/api/alerts")
async def alerts(limit: int = 50, user: dict = Depends(require_auth)) -> list[dict]:
    rows = repo.get_recent_alerts(limit=min(limit, 100))
    return [
        {
            "id": a.id,
            "wallet_address": a.wallet_address,
            "alert_type": a.alert_type,
            "market_id": a.market_id,
            "market_question": a.market_question,
            "side": a.side,
            "size": a.size,
            "price": a.price,
            "alerted_at": a.alerted_at.isoformat(),
        }
        for a in rows
    ]


# ── Strategy analysis endpoints ───────────────────────────────────────────────

# In-memory job tracking: job_id -> {status, result, error, created_at}
_jobs: dict[str, dict] = {}

# Rate limit tracking: user_id -> {date: date, count: int}
_regen_limits: dict[str, dict] = {}


def _check_regen_rate_limit(user_id: str) -> bool:
    """Return True if under the daily limit, False if exceeded. Increments counter."""
    today = datetime.utcnow().date()
    entry = _regen_limits.get(user_id)
    if entry is None or entry["date"] != today:
        _regen_limits[user_id] = {"date": today, "count": 0}
        entry = _regen_limits[user_id]
    if entry["count"] >= STRATEGY_REGEN_DAILY_LIMIT:
        return False
    entry["count"] += 1
    return True


def _serialize_strategy(s) -> dict:
    return {
        "id": s.id,
        "wallet_address": s.wallet_address,
        "is_replicable": s.is_replicable,
        "replicability_confidence": s.replicability_confidence,
        "capital_required_min_usd": s.capital_required_min_usd,
        "strategy_type": s.strategy_type,
        "strategy_subtype": s.strategy_subtype,
        "entry_signal": s.entry_signal,
        "exit_signal": s.exit_signal,
        "position_sizing_rule": s.position_sizing_rule,
        "market_selection_criteria": s.market_selection_criteria,
        "infrastructure_required": s.infrastructure_required,
        "estimated_hit_rate": s.estimated_hit_rate,
        "estimated_avg_hold_time_hours": s.estimated_avg_hold_time_hours,
        "estimated_sharpe_proxy": s.estimated_sharpe_proxy,
        "failure_modes": _json_list(s.failure_modes),
        "risk_factors": _json_list(s.risk_factors),
        "prompt_version": s.prompt_version,
        "model_used": s.model_used,
        "generated_at": s.generated_at.isoformat(),
        "wallet_state_snapshot": _json_dict(s.wallet_state_snapshot),
        "full_thesis": s.full_thesis,
        "paper_trade_recommendation": s.paper_trade_recommendation,
    }


def _json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def _json_dict(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


@app.get("/api/wallet/{address}/strategy")
async def get_wallet_strategy(address: str, user: dict = Depends(require_auth)) -> dict:
    """Return the most recent strategy analysis for a wallet, or 404 if none exists."""
    analysis = repo.get_latest_strategy_analysis(address)
    if analysis is None:
        raise HTTPException(status_code=404, detail="No strategy analysis found for this wallet")
    return _serialize_strategy(analysis)


@app.get("/api/wallet/{address}/strategy/history")
async def get_wallet_strategy_history(address: str, user: dict = Depends(require_auth)) -> list[dict]:
    """Return all strategy analyses for a wallet, newest first."""
    analyses = repo.get_strategy_analysis_history(address)
    return [_serialize_strategy(a) for a in analyses]


@app.post("/api/wallet/{address}/strategy/regenerate")
async def regenerate_wallet_strategy(
    address: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_auth),
) -> dict:
    """Trigger fresh strategy analysis for a wallet. Rate-limited to 5/day per user.

    Returns a job_id for polling at GET /api/jobs/{job_id}.
    """
    user_id = user.get("id", "unknown")
    if not _check_regen_rate_limit(user_id):
        raise HTTPException(
            status_code=429,
            detail=f"Daily regeneration limit ({STRATEGY_REGEN_DAILY_LIMIT}) reached. Try again tomorrow.",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "result": None, "error": None, "created_at": datetime.utcnow().isoformat()}

    background_tasks.add_task(_run_strategy_job, job_id, address)
    return {"job_id": job_id, "status": "pending"}


async def _run_strategy_job(job_id: str, address: str) -> None:
    from analysis.strategy_analyzer import analyze_wallet_strategy

    _jobs[job_id]["status"] = "running"
    try:
        analysis = await analyze_wallet_strategy(address)
        if analysis is None:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "Analysis failed or returned no result"
            return
        saved = repo.save_strategy_analysis(analysis)
        _jobs[job_id]["status"] = "complete"
        _jobs[job_id]["result"] = _serialize_strategy(saved)
    except Exception as exc:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str, user: dict = Depends(require_auth)) -> dict:
    """Poll for async job status. Returns status and result when complete."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


@app.get("/api/wallets/{address}")
async def wallet_detail(address: str, user: dict = Depends(require_auth)) -> dict:
    ranking = repo.get_ranking_for_wallet(address)
    metrics = repo.get_metrics_for_wallet(address)
    if ranking is None and metrics is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    flags: list[str] = []
    if ranking:
        for field in (ranking.heuristic_red_flags, ranking.claude_red_flags):
            if field:
                try:
                    flags += json.loads(field)
                except json.JSONDecodeError:
                    pass
    return {
        "address": address,
        "ranking": {
            "rank": ranking.rank,
            "composite_score": ranking.composite_score,
            "skill_signal": ranking.skill_signal,
            "edge_hypothesis": ranking.edge_hypothesis,
            "claude_notes": ranking.claude_notes,
            "red_flags": flags,
            "ranked_at": ranking.ranked_at.isoformat(),
        } if ranking else None,
        "metrics": {
            "trade_count": metrics.trade_count,
            "total_pnl": metrics.total_pnl,
            "total_volume": metrics.total_volume,
            "portfolio_value": metrics.portfolio_value,
            "realized_position_count": metrics.realized_position_count,
            "unresolved_position_count": metrics.unresolved_position_count,
            "avg_position_size": metrics.avg_position_size,
            "max_position_size_usd": metrics.max_position_size_usd,
            "pct_pnl_from_top_3_positions": metrics.pct_pnl_from_top_3_positions,
            "market_count": metrics.market_count,
            "computed_at": metrics.computed_at.isoformat(),
        } if metrics else None,
    }
