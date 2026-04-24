from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so scanner/data/config modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI(title="Wallet Scanner", docs_url=None, redoc_url=None)
security = HTTPBasic()

_DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not _DASHBOARD_USER or not _DASHBOARD_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set DASHBOARD_USER and DASHBOARD_PASSWORD environment variables.",
        )
    ok_user = secrets.compare_digest(
        credentials.username.encode(), _DASHBOARD_USER.encode()
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode(), _DASHBOARD_PASSWORD.encode()
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _load_rankings(limit: int = 100) -> list[dict[str, Any]]:
    try:
        from data.database import init_db
        from scanner import repository as repo

        init_db()
        rankings = repo.get_top_rankings(limit=limit)
        rows: list[dict[str, Any]] = []
        for r in rankings:
            m = repo.get_metrics_for_wallet(r.wallet_address)
            flags: list[str] = []
            for field_val in (r.heuristic_red_flags, r.claude_red_flags):
                if field_val:
                    try:
                        flags += json.loads(field_val)
                    except json.JSONDecodeError:
                        pass
            rows.append(
                {
                    "rank": r.rank,
                    "address": r.wallet_address,
                    "composite_score": round(r.composite_score, 4),
                    "win_rate": round(m.win_rate, 4)
                    if m and m.win_rate is not None
                    else None,
                    "total_pnl": round(m.total_pnl, 2)
                    if m and m.total_pnl is not None
                    else None,
                    "total_volume": round(m.total_volume, 2)
                    if m and m.total_volume is not None
                    else None,
                    "trade_count": m.trade_count if m else None,
                    "sharpe": round(m.sharpe_ratio, 3)
                    if m and m.sharpe_ratio is not None
                    else None,
                    "profit_factor": round(m.profit_factor, 3)
                    if m and m.profit_factor is not None
                    else None,
                    "skill_signal": round(r.skill_signal, 2)
                    if r.skill_signal is not None
                    else None,
                    "edge_hypothesis": r.edge_hypothesis or "",
                    "red_flags": flags,
                    "ranked_at": r.ranked_at.isoformat()
                    if isinstance(r.ranked_at, datetime)
                    else str(r.ranked_at),
                }
            )
        return rows
    except Exception:
        return []


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Wallet Scanner</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,monospace;background:#0d1117;color:#c9d1d9;min-height:100vh}}
    header{{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;gap:10px}}
    header h1{{font-size:1rem;font-weight:600;color:#f0f6fc}}
    .badge{{background:#238636;color:#fff;font-size:.7rem;padding:2px 8px;border-radius:20px}}
    .wrap{{max-width:1200px;margin:0 auto;padding:20px}}
    .meta{{color:#8b949e;font-size:.82rem;margin-bottom:14px}}
    .empty{{text-align:center;padding:80px 20px;color:#8b949e}}
    .empty code{{display:inline-block;margin-top:10px;background:#161b22;padding:6px 14px;border-radius:6px;color:#79c0ff;font-size:.85rem}}
    .scroll{{overflow-x:auto}}
    table{{width:100%;border-collapse:collapse;font-size:.83rem}}
    th{{background:#161b22;color:#8b949e;font-weight:600;padding:8px 10px;text-align:left;border-bottom:1px solid #30363d;white-space:nowrap}}
    td{{padding:8px 10px;border-bottom:1px solid #21262d;vertical-align:middle}}
    tr:hover td{{background:#161b22}}
    .dim{{color:#8b949e;font-weight:600}}
    .addr{{font-family:monospace;color:#79c0ff;font-size:.78rem}}
    .bold{{font-weight:600;color:#f0f6fc}}
    .pos{{color:#3fb950}}.neg{{color:#f85149}}
    .flag{{background:#3d1f1f;color:#f85149;border-radius:3px;padding:1px 5px;font-size:.72rem;margin-right:2px}}
    @media(max-width:700px){{.hm{{display:none}}.wrap{{padding:10px}}}}
  </style>
</head>
<body>
  <header>
    <h1>Wallet Scanner</h1>
    <span class="badge">Polymarket</span>
  </header>
  <div class="wrap">
    {body}
  </div>
</body>
</html>"""


def _render(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            '<div class="empty">'
            "<p>No wallet data yet.</p>"
            '<p style="margin-top:10px;font-size:.82rem">Run a scan locally then sync the database.</p>'
            "<br><code>python main.py scan</code>"
            "</div>"
        )

    def addr(a: str) -> str:
        return f"{a[:8]}…{a[-6:]}" if len(a) > 14 else a

    def pnl(v: float | None) -> str:
        if v is None:
            return "–"
        cls = "pos" if v > 0 else "neg" if v < 0 else ""
        return f'<span class="{cls}">${v:,.0f}</span>'

    def flags(fs: list[str]) -> str:
        if not fs:
            return '<span class="pos">✓</span>'
        return "".join(f'<span class="flag">{f}</span>' for f in fs)

    last_updated = rows[0]["ranked_at"][:10] if rows else "never"

    def _row(r: dict[str, Any]) -> str:
        win = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "–"
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "–"
        trades = str(r["trade_count"]) if r["trade_count"] is not None else "–"
        skill = f"{r['skill_signal']:.2f}" if r["skill_signal"] is not None else "–"
        return (
            "<tr>"
            f'<td class="dim">{r["rank"]}</td>'
            f'<td class="addr" title="{r["address"]}">{addr(r["address"])}</td>'
            f'<td class="bold">{r["composite_score"]:.4f}</td>'
            f"<td>{win}</td>"
            f'<td class="hm">{sharpe}</td>'
            f'<td class="hm">{pnl(r["total_pnl"])}</td>'
            f'<td class="hm">{trades}</td>'
            f'<td class="hm">{skill}</td>'
            f"<td>{flags(r['red_flags'])}</td>"
            "</tr>"
        )

    trs = "\n".join(_row(r) for r in rows)

    return (
        f'<p class="meta">{len(rows)} wallets &mdash; updated {last_updated}</p>'
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>#</th><th>Address</th><th>Score</th><th>Win%</th>"
        '<th class="hm">Sharpe</th><th class="hm">P&amp;L</th>'
        '<th class="hm">Trades</th><th class="hm">Skill</th><th>Flags</th>'
        "</tr></thead>"
        f"<tbody>{trs}</tbody></table></div>"
    )


@app.get("/", response_class=HTMLResponse)
def index(_: str = Depends(require_auth)) -> HTMLResponse:
    rows = _load_rankings(limit=100)
    return HTMLResponse(_HTML.format(body=_render(rows)))


@app.get("/api/leaderboard")
def leaderboard(limit: int = 50, _: str = Depends(require_auth)) -> JSONResponse:
    rows = _load_rankings(limit=min(limit, 200))
    return JSONResponse(rows)
