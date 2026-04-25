from __future__ import annotations

import json
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from data.database import init_db
from scanner import repository as repo

app = FastAPI(title="Wallet Scanner", docs_url=None, redoc_url=None)

_security = HTTPBasic()
_DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
_DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "")

# Initialize DB tables on cold start (no-op if DB is unavailable)
try:
    init_db()
except Exception:
    pass


def _auth(creds: HTTPBasicCredentials = Depends(_security)) -> None:
    if not _DASHBOARD_USER or not _DASHBOARD_PASS:
        return
    ok = secrets.compare_digest(creds.username.encode(), _DASHBOARD_USER.encode()) and \
         secrets.compare_digest(creds.password.encode(), _DASHBOARD_PASS.encode())
    if not ok:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


@app.get("/", response_class=HTMLResponse)
def root(_: None = Depends(_auth)) -> str:
    return "<meta http-equiv='refresh' content='0; url=/api/leaderboard'>"


@app.get("/api/leaderboard")
def leaderboard(limit: int = 50, _: None = Depends(_auth)) -> list[dict]:
    rows = repo.get_top_rankings(limit=min(limit, 200))
    result = []
    for r in rows:
        metrics = repo.get_metrics_for_wallet(r.wallet_address)
        flags: list[str] = []
        for field in (r.heuristic_red_flags, r.claude_red_flags):
            if field:
                try:
                    flags += json.loads(field)
                except json.JSONDecodeError:
                    pass
        result.append({
            "address": r.wallet_address,
            "rank": r.rank,
            "composite_score": r.composite_score,
            "skill_signal": r.skill_signal,
            "edge_hypothesis": r.edge_hypothesis,
            "red_flags": flags,
            "metrics": {
                "trade_count": metrics.trade_count,
                "win_rate": metrics.win_rate,
                "sharpe_ratio": metrics.sharpe_ratio,
                "total_pnl": metrics.total_pnl,
                "total_volume": metrics.total_volume,
                "profit_factor": metrics.profit_factor,
                "market_count": metrics.market_count,
            } if metrics else None,
        })
    return result


@app.get("/api/alerts")
def alerts(limit: int = 50, _: None = Depends(_auth)) -> list[dict]:
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
def wallet_detail(address: str, _: None = Depends(_auth)) -> dict:
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
            "win_rate": metrics.win_rate,
            "sharpe_ratio": metrics.sharpe_ratio,
            "total_pnl": metrics.total_pnl,
            "total_volume": metrics.total_volume,
            "profit_factor": metrics.profit_factor,
            "avg_hold_time_hours": metrics.avg_hold_time_hours,
            "market_count": metrics.market_count,
            "computed_at": metrics.computed_at.isoformat(),
        } if metrics else None,
    }
