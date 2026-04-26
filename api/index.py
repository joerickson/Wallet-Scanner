from __future__ import annotations

import json
import pathlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.auth import AUTH_ENABLED, handle_callback, require_auth, signout_response, start_oauth, validate_session
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
    return {"status": "ok", "ranked_wallet_count": total, "last_scan_at": last_scan_at}


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
