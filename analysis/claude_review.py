from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MAX_TOKENS, CLAUDE_SCANNER_MODEL
from data.schema import Position, WalletMetrics
from scanner import repository as repo

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


_SYSTEM_PROMPT = """\
You are an expert quantitative analyst reviewing Polymarket prediction-market traders.
Polymarket is a prediction-market platform; traders buy YES/NO shares on future events.

Your job: assess whether a wallet's performance reflects genuine skill or statistical noise.

Respond ONLY with valid JSON matching this exact schema — no prose, no markdown:
{
  "skill_signal": <float 0.0–1.0>,
  "edge_hypothesis": "<one concise sentence about the likely edge source>",
  "red_flags": ["<flag_1>", ...],
  "notes": "<2-3 sentence qualitative summary>"
}

skill_signal: 0.0 = pure luck/artefact, 1.0 = strong evidence of repeatable skill.
red_flags: empty list if none. Possible flags: "single_event_luck", "data_artefact",
  "recency_cliff", "market_concentration", "survivorship", "single_bet_dominance".
"""


def _build_prompt(
    address: str,
    metrics: WalletMetrics,
    top_positions: list[Position],
    heuristic_flags: list[str],
    leaderboard_rank: int | None,
) -> str:
    lines = [
        f"Wallet: {address}",
    ]
    if leaderboard_rank:
        lines.append(f"Leaderboard rank: #{leaderboard_rank}")
    lines += [
        "",
        "## Summary Metrics",
        f"- Total P&L: ${metrics.total_pnl:,.2f}" if metrics.total_pnl is not None else "- Total P&L: N/A",
        f"- Total volume: ${metrics.total_volume:,.2f}" if metrics.total_volume is not None else "- Total volume: N/A",
        f"- Portfolio value: ${metrics.portfolio_value:,.2f}" if metrics.portfolio_value is not None else "- Portfolio value: N/A",
        f"- Positions traded: {metrics.trade_count} ({metrics.realized_position_count} resolved, {metrics.unresolved_position_count} unresolved)",
        f"- Markets: {metrics.market_count}",
        f"- Top market concentration: {metrics.top_market_concentration:.1%}" if metrics.top_market_concentration is not None else "- Top market concentration: N/A",
        f"- P&L from top 3 positions: {metrics.pct_pnl_from_top_3_positions:.1%}" if metrics.pct_pnl_from_top_3_positions is not None else "- P&L from top 3 positions: N/A",
    ]

    if top_positions:
        lines += ["", "## Top 5 Positions by Absolute P&L"]
        for i, pos in enumerate(top_positions[:5], start=1):
            status = "RESOLVED ✓" if pos.redeemable else "unresolved"
            pnl_str = f"${pos.cash_pnl:,.2f}" if pos.cash_pnl is not None else "N/A"
            title = (pos.title or pos.condition_id)[:60]
            outcome = pos.outcome or "?"
            lines.append(f"{i}. \"{title}\" {outcome} — Cash P&L: {pnl_str} — {status}")

    if heuristic_flags:
        lines += ["", "## Heuristic Red Flags Already Detected"]
        for flag in heuristic_flags:
            lines.append(f"- {flag}")

    return "\n".join(lines)


async def review_wallet(
    address: str,
    metrics: WalletMetrics,
    leaderboard_rank: int | None = None,
) -> dict[str, Any] | None:
    """
    Send one wallet's metrics + top positions to Claude for qualitative review.
    Returns parsed JSON dict or None if the call fails or produces invalid JSON.
    """
    positions = repo.get_positions_for_wallet(address)
    top_positions = sorted(
        [p for p in positions if p.cash_pnl is not None],
        key=lambda p: abs(p.cash_pnl),  # type: ignore[arg-type]
        reverse=True,
    )[:5]

    ranking = repo.get_ranking_for_wallet(address)
    heuristic_flags: list[str] = []
    if ranking and ranking.heuristic_red_flags:
        try:
            heuristic_flags = json.loads(ranking.heuristic_red_flags)
        except json.JSONDecodeError:
            pass

    prompt = _build_prompt(address, metrics, top_positions, heuristic_flags, leaderboard_rank)

    try:
        client = _get_client()
        message = await client.messages.create(
            model=CLAUDE_SCANNER_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return _parse_response(raw, address)
    except anthropic.APIError as exc:
        logger.warning("Claude API error for %s: %s", address, exc)
        return None


def _parse_response(raw: str, address: str) -> dict[str, Any] | None:
    """Parse and validate the Claude JSON response. Logs and returns None on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "JSON parse failed for Claude response on %s: %s\nRaw: %s",
            address, exc, raw[:300],
        )
        return None

    skill = data.get("skill_signal")
    if not isinstance(skill, (int, float)) or not (0.0 <= float(skill) <= 1.0):
        logger.warning("Invalid skill_signal in Claude response for %s", address)
        data["skill_signal"] = None

    if "red_flags" not in data or not isinstance(data["red_flags"], list):
        data["red_flags"] = []

    return {
        "skill_signal": float(data["skill_signal"]) if data.get("skill_signal") is not None else None,
        "edge_hypothesis": str(data.get("edge_hypothesis") or ""),
        "red_flags": [str(f) for f in data.get("red_flags", [])],
        "notes": str(data.get("notes") or ""),
    }
