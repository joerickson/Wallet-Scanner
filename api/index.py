from __future__ import annotations

import json
import logging
import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.auth import AUTH_ENABLED, NEON_AUTH_BASE_URL, require_auth
from config import STRATEGY_REGEN_DAILY_LIMIT
from data.database import get_engine, init_db
from data.schema import PaperTest, PaperTrade
from scanner import repository as repo
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

app = FastAPI(title="Wallet Scanner", docs_url=None, redoc_url=None)

_DIST_DIR = pathlib.Path(__file__).parent.parent / "dashboard" / "dist"

try:
    init_db()
except Exception:
    pass


@app.get("/api/config")
def get_config() -> dict:
    """Return public frontend configuration (Neon Auth URL for direct SDK calls)."""
    return {"neon_auth_url": NEON_AUTH_BASE_URL if AUTH_ENABLED else ""}


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
        "paper_test_filter": _json_dict(s.paper_test_filter) if s.paper_test_filter else None,
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


# ── Polymarket endpoints ──────────────────────────────────────────────────────


@app.get("/api/polymarket/test")
async def polymarket_test(sport: str, user: dict = Depends(require_auth)) -> list[dict]:
    """Return the first 5 open markets for a given sport. Used to manually verify the client."""
    from api.polymarket import search_markets

    markets = await search_markets({"sports": [sport], "leagues": [], "status": "open"})
    return [m.model_dump(mode="json") for m in markets[:5]]


# ── Paper test helpers ────────────────────────────────────────────────────────

def _serialize_paper_test(pt: PaperTest, trades: list[PaperTrade] | None = None) -> dict:
    out: dict = {
        "id": pt.id,
        "wallet_address": pt.wallet_address,
        "strategy_analysis_id": pt.strategy_analysis_id,
        "user_id": pt.user_id,
        "capital_allocated": float(pt.capital_allocated),
        "started_at": pt.started_at.isoformat(),
        "ends_at": pt.ends_at.isoformat(),
        "status": pt.status,
        "realized_pnl": float(pt.realized_pnl),
        "unrealized_pnl": float(pt.unrealized_pnl),
        "last_evaluated_at": pt.last_evaluated_at.isoformat() if pt.last_evaluated_at else None,
        "filter_snapshot": json.loads(pt.filter_snapshot) if pt.filter_snapshot else {},
        "created_at": pt.created_at.isoformat(),
    }
    if trades is not None:
        out["trades"] = [_serialize_paper_trade(t) for t in trades]
    return out


def _serialize_paper_trade(t: PaperTrade) -> dict:
    return {
        "id": t.id,
        "paper_test_id": t.paper_test_id,
        "polymarket_condition_id": t.polymarket_condition_id,
        "market_question": t.market_question,
        "outcome_name": t.outcome_name,
        "token_id": t.token_id,
        "side": t.side,
        "entry_price": float(t.entry_price),
        "entry_size_usd": float(t.entry_size_usd),
        "entry_at": t.entry_at.isoformat(),
        "exit_price": float(t.exit_price) if t.exit_price is not None else None,
        "exit_at": t.exit_at.isoformat() if t.exit_at else None,
        "exit_reason": t.exit_reason,
        "realized_pnl": float(t.realized_pnl) if t.realized_pnl is not None else None,
        "status": t.status,
    }


# ── Paper test endpoints ──────────────────────────────────────────────────────


@app.post("/api/paper-tests")
async def create_paper_test(request: Request, user: dict = Depends(require_auth)) -> dict:
    body = await request.json()
    wallet_address = (body.get("wallet_address") or "").strip()
    strategy_analysis_id = body.get("strategy_analysis_id")
    capital_allocated = float(body.get("capital_allocated") or 10000)

    if not wallet_address:
        raise HTTPException(status_code=400, detail="wallet_address is required")
    if not strategy_analysis_id:
        raise HTTPException(status_code=400, detail="strategy_analysis_id is required")

    with Session(get_engine(), expire_on_commit=False) as s:
        from data.schema import WalletStrategyAnalysis
        analysis = s.get(WalletStrategyAnalysis, int(strategy_analysis_id))
        if analysis is None:
            raise HTTPException(status_code=404, detail="Strategy analysis not found")
        if not analysis.paper_test_filter:
            raise HTTPException(status_code=400, detail="This strategy has no paper_test_filter")

        try:
            filter_data = json.loads(analysis.paper_test_filter)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid paper_test_filter data")

        duration_days = float(filter_data.get("duration_days") or 7)
        now = datetime.now(timezone.utc)
        pt = PaperTest(
            id=str(uuid.uuid4()),
            wallet_address=wallet_address,
            strategy_analysis_id=int(strategy_analysis_id),
            user_id=user["id"],
            capital_allocated=capital_allocated,
            started_at=now,
            ends_at=now + timedelta(days=duration_days),
            status="running",
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            filter_snapshot=json.dumps(filter_data),
            created_at=now,
        )
        s.add(pt)
        s.commit()
        s.refresh(pt)
        return _serialize_paper_test(pt, trades=[])


@app.get("/api/paper-tests")
async def list_paper_tests(
    wallet_address: str | None = None,
    user: dict = Depends(require_auth),
) -> list[dict]:
    with Session(get_engine(), expire_on_commit=False) as s:
        stmt = select(PaperTest).where(PaperTest.user_id == user["id"])
        if wallet_address:
            stmt = stmt.where(PaperTest.wallet_address == wallet_address)
        stmt = stmt.order_by(PaperTest.started_at.desc())
        tests = list(s.exec(stmt).all())
        return [_serialize_paper_test(pt) for pt in tests]


@app.get("/api/paper-tests/{test_id}")
async def get_paper_test(test_id: str, user: dict = Depends(require_auth)) -> dict:
    with Session(get_engine(), expire_on_commit=False) as s:
        pt = s.get(PaperTest, test_id)
        if pt is None:
            raise HTTPException(status_code=404, detail="Paper test not found")
        if pt.user_id != user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")
        trades_stmt = select(PaperTrade).where(PaperTrade.paper_test_id == test_id)
        trades = list(s.exec(trades_stmt).all())
        return _serialize_paper_test(pt, trades=trades)


@app.post("/api/paper-tests/{test_id}/cancel")
async def cancel_paper_test(test_id: str, user: dict = Depends(require_auth)) -> dict:
    from api.polymarket import get_price

    with Session(get_engine(), expire_on_commit=False) as s:
        pt = s.get(PaperTest, test_id)
        if pt is None:
            raise HTTPException(status_code=404, detail="Paper test not found")
        if pt.user_id != user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")
        if pt.status != "running":
            raise HTTPException(status_code=400, detail="Paper test is not running")

        now = datetime.now(timezone.utc)
        trades_stmt = select(PaperTrade).where(
            PaperTrade.paper_test_id == test_id,
            PaperTrade.status == "open",
        )
        open_trades = list(s.exec(trades_stmt).all())

        total_realized = float(pt.realized_pnl)
        for trade in open_trades:
            try:
                current_price = await get_price(trade.token_id)
            except Exception:
                current_price = float(trade.entry_price)
            rpnl = (current_price - float(trade.entry_price)) * float(trade.entry_size_usd) / float(trade.entry_price)
            trade.exit_price = current_price
            trade.exit_at = now
            trade.exit_reason = "manual"
            trade.realized_pnl = rpnl
            trade.status = "closed"
            s.add(trade)
            total_realized += rpnl

        pt.status = "completed"
        pt.realized_pnl = total_realized
        pt.unrealized_pnl = 0.0
        pt.last_evaluated_at = now
        s.add(pt)
        s.commit()
        s.refresh(pt)
        return _serialize_paper_test(pt)


@app.post("/api/cron/advance-paper-tests")
async def advance_paper_tests(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> dict:
    cron_secret = os.environ.get("CRON_SECRET", "")
    if not cron_secret or x_cron_secret != cron_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from api.polymarket import get_price, search_markets

    now = datetime.now(timezone.utc)
    processed = 0
    errors = 0

    with Session(get_engine(), expire_on_commit=False) as s:
        running_stmt = select(PaperTest).where(
            PaperTest.status == "running",
            PaperTest.ends_at > now,
        )
        running_tests = list(s.exec(running_stmt).all())

    for pt in running_tests:
        try:
            await _advance_single_paper_test(pt.id, now)
            processed += 1
        except Exception as exc:
            logger.error("advance_paper_tests: error on paper_test %s: %s", pt.id, exc)
            errors += 1

    # Handle expired tests
    with Session(get_engine(), expire_on_commit=False) as s:
        expired_stmt = select(PaperTest).where(
            PaperTest.status == "running",
            PaperTest.ends_at <= now,
        )
        expired_tests = list(s.exec(expired_stmt).all())

    for pt in expired_tests:
        try:
            await _close_expired_paper_test(pt.id, now)
            processed += 1
        except Exception as exc:
            logger.error("advance_paper_tests: error closing expired test %s: %s", pt.id, exc)
            errors += 1

    return {"processed": processed, "errors": errors}


async def _advance_single_paper_test(test_id: str, now: datetime) -> None:
    from api.polymarket import get_market, get_price, get_orderbook, search_markets

    with Session(get_engine(), expire_on_commit=False) as s:
        pt = s.get(PaperTest, test_id)
        if pt is None or pt.status != "running":
            return

        try:
            filter_data = json.loads(pt.filter_snapshot)
        except (json.JSONDecodeError, TypeError):
            filter_data = {}

        open_trades_stmt = select(PaperTrade).where(
            PaperTrade.paper_test_id == test_id,
            PaperTrade.status == "open",
        )
        open_trades = list(s.exec(open_trades_stmt).all())

        # Step 1: If no open trades, look for entry candidates (cap at 1 per test)
        if not open_trades:
            try:
                candidates = await search_markets(filter_data)
                # v1: binary markets only
                binary = [m for m in candidates if len(m.outcomes) == 2]
                for market in binary[:5]:
                    entry_conds = filter_data.get("entry_conditions") or []
                    should_enter = False

                    if not entry_conds:
                        should_enter = True

                    for cond in entry_conds:
                        ctype = cond.get("type") or ""
                        value = cond.get("value")

                        if ctype == "combined_cost_below":
                            try:
                                books = []
                                for outcome in market.outcomes:
                                    if outcome.token_id:
                                        book = await get_orderbook(outcome.token_id)
                                        best_ask = min((a[0] for a in book.asks), default=None)
                                        if best_ask is not None:
                                            books.append(best_ask)
                                if books and value is not None and sum(books) < float(value):
                                    should_enter = True
                            except Exception:
                                pass

                        elif ctype == "single_side_discount_below":
                            try:
                                for outcome in market.outcomes:
                                    if outcome.token_id:
                                        book = await get_orderbook(outcome.token_id)
                                        best_ask = min((a[0] for a in book.asks), default=None)
                                        if best_ask is not None and value is not None and best_ask < float(value):
                                            should_enter = True
                                            break
                            except Exception:
                                pass

                        elif ctype in ("spread_above", "custom"):
                            logger.info("Skipping v1-unsupported entry condition: %s", ctype)
                            continue

                    if should_enter and market.outcomes:
                        # Pick the first outcome to trade (YES side)
                        outcome = market.outcomes[0]
                        if not outcome.token_id:
                            continue
                        try:
                            entry_price = await get_price(outcome.token_id)
                        except Exception:
                            entry_price = outcome.current_price or 0.5

                        # Position sizing
                        pos_sizing = filter_data.get("position_sizing") or {}
                        pct = float(pos_sizing.get("pct_of_capital") or 0.1)
                        min_size = float(pos_sizing.get("min_size_usd") or 100)
                        max_size = float(pos_sizing.get("max_size_usd") or 5000)
                        raw_size = float(pt.capital_allocated) * pct
                        entry_size = max(min_size, min(raw_size, max_size))

                        trade = PaperTrade(
                            id=str(uuid.uuid4()),
                            paper_test_id=test_id,
                            polymarket_condition_id=market.condition_id,
                            market_question=market.question,
                            outcome_name=outcome.name,
                            token_id=outcome.token_id,
                            side="buy",
                            entry_price=entry_price,
                            entry_size_usd=entry_size,
                            entry_at=now,
                            status="open",
                        )
                        s.add(trade)
                        open_trades = [trade]
                        break  # v1: cap at one open trade per test
            except Exception as exc:
                logger.warning("advance_paper_test %s: entry search failed: %s", test_id, exc)

        # Step 2: Evaluate exit conditions on open trades
        total_realized = float(pt.realized_pnl)
        total_unrealized = 0.0

        for trade in open_trades:
            try:
                current_price = await get_price(trade.token_id)
            except Exception:
                current_price = float(trade.entry_price)

            exit_conds = filter_data.get("exit_conditions") or []
            should_exit = False
            exit_reason_val = None

            for cond in exit_conds:
                ctype = cond.get("type") or ""
                value = cond.get("value")

                if ctype == "price_move_pct_in_favor":
                    if value is not None:
                        move_pct = (current_price - float(trade.entry_price)) / max(float(trade.entry_price), 0.001)
                        if move_pct >= float(value) / 100.0:
                            should_exit = True
                            exit_reason_val = "price_move"

                elif ctype == "resolution":
                    try:
                        market_info = await get_market(trade.polymarket_condition_id)
                        if market_info.end_date and market_info.end_date <= now:
                            should_exit = True
                            exit_reason_val = "resolution"
                    except Exception:
                        pass

                elif ctype == "time_in_position_hours":
                    if value is not None:
                        hours_held = (now - trade.entry_at.replace(tzinfo=timezone.utc) if trade.entry_at.tzinfo is None else now - trade.entry_at).total_seconds() / 3600
                        if hours_held >= float(value):
                            should_exit = True
                            exit_reason_val = "time"

                elif ctype in ("hedge_ratio_suboptimal", "custom"):
                    logger.info("Skipping v1-unsupported exit condition: %s", ctype)
                    continue

            if should_exit:
                rpnl = (current_price - float(trade.entry_price)) * float(trade.entry_size_usd) / max(float(trade.entry_price), 0.001)
                trade.exit_price = current_price
                trade.exit_at = now
                trade.exit_reason = exit_reason_val
                trade.realized_pnl = rpnl
                trade.status = "closed"
                s.add(trade)
                total_realized += rpnl
            else:
                unrealized = (current_price - float(trade.entry_price)) * float(trade.entry_size_usd) / max(float(trade.entry_price), 0.001)
                total_unrealized += unrealized

        pt.realized_pnl = total_realized
        pt.unrealized_pnl = total_unrealized
        pt.last_evaluated_at = now
        s.add(pt)
        s.commit()


async def _close_expired_paper_test(test_id: str, now: datetime) -> None:
    from api.polymarket import get_price

    with Session(get_engine(), expire_on_commit=False) as s:
        pt = s.get(PaperTest, test_id)
        if pt is None:
            return

        open_trades_stmt = select(PaperTrade).where(
            PaperTrade.paper_test_id == test_id,
            PaperTrade.status == "open",
        )
        open_trades = list(s.exec(open_trades_stmt).all())

        total_realized = float(pt.realized_pnl)
        for trade in open_trades:
            try:
                current_price = await get_price(trade.token_id)
            except Exception:
                current_price = float(trade.entry_price)
            rpnl = (current_price - float(trade.entry_price)) * float(trade.entry_size_usd) / max(float(trade.entry_price), 0.001)
            trade.exit_price = current_price
            trade.exit_at = now
            trade.exit_reason = "time"
            trade.realized_pnl = rpnl
            trade.status = "closed"
            s.add(trade)
            total_realized += rpnl

        pt.status = "completed"
        pt.realized_pnl = total_realized
        pt.unrealized_pnl = 0.0
        pt.last_evaluated_at = now
        s.add(pt)
        s.commit()


# ── Static assets and SPA fallback ───────────────────────────────────────────
# Mount /assets only when the dist directory has been built.
# All non-API GET requests fall through to the React SPA's index.html.

if _DIST_DIR.exists() and (_DIST_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST_DIR / "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    index = _DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse(  # type: ignore[return-value]
        "<p>Frontend not built. Run: <code>cd dashboard && npm run build</code></p>",
        status_code=503,
    )
