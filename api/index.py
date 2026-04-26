from __future__ import annotations

import json
import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from data.database import init_db
from scanner import repository as repo

app = FastAPI(title="Wallet Scanner", docs_url=None, redoc_url=None)

_DASHBOARD_HTML = pathlib.Path(__file__).parent.parent / "dashboard" / "index.html"

# Initialize DB tables on cold start (no-op if DB is unavailable)
try:
    init_db()
except Exception:
    pass


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    try:
        return _DASHBOARD_HTML.read_text()
    except FileNotFoundError:
        return "<p>Dashboard not found.</p>"


@app.get("/api/leaderboard")
def leaderboard(limit: int = 50) -> dict:
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


@app.get("/api/alerts")
def alerts(limit: int = 50) -> list[dict]:
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
def wallet_detail(address: str) -> dict:
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
