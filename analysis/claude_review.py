from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from analysis.patterns import extract_patterns, patterns_to_dict
from config import ANTHROPIC_API_KEY, CLAUDE_MAX_TOKENS, CLAUDE_SCANNER_MODEL
from data.schema import WalletMetrics
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
red_flags: empty list if none. Possible flags: "insider_timing", "market_manipulation",
  "single_event_luck", "data_artefact", "recency_cliff", "market_concentration",
  "survivorship_bias", "volume_size_mismatch".
"""


def _build_prompt(
    address: str,
    metrics: WalletMetrics,
    patterns: dict[str, Any],
    heuristic_flags: list[str],
) -> str:
    m = metrics
    lines = [
        f"Wallet: {address}",
        "",
        "## Quantitative Metrics",
        f"- Trade count: {m.trade_count}",
        f"- Win rate: {m.win_rate:.1%}" if m.win_rate is not None else "- Win rate: N/A",
        f"- Total P&L: ${m.total_pnl:,.2f}" if m.total_pnl is not None else "- Total P&L: N/A",
        f"- Total volume: ${m.total_volume:,.2f}" if m.total_volume is not None else "- Total volume: N/A",
        f"- Sharpe ratio: {m.sharpe_ratio:.3f}" if m.sharpe_ratio is not None else "- Sharpe ratio: N/A (insufficient trades)",
        f"- Profit factor: {m.profit_factor:.3f}" if m.profit_factor is not None else "- Profit factor: N/A",
        f"- Market count: {m.market_count}",
        f"- Top market concentration: {m.top_market_concentration:.1%}" if m.top_market_concentration is not None else "- Top market concentration: N/A",
        f"- Exit quality: {m.exit_quality:.3f}" if m.exit_quality is not None else "- Exit quality: N/A",
        "",
        "## Behaviour Patterns",
    ]
    for k, v in patterns.items():
        if v is not None:
            lines.append(f"- {k}: {v}")

    if heuristic_flags:
        lines.append("")
        lines.append("## Heuristic Red Flags Already Detected")
        for flag in heuristic_flags:
            lines.append(f"- {flag}")

    return "\n".join(lines)


async def review_wallet(
    address: str, metrics: WalletMetrics
) -> dict[str, Any] | None:
    """
    Send one wallet's metrics + patterns to Claude for qualitative review.
    Returns parsed JSON dict or None if the call fails or produces invalid JSON.
    """
    trades = repo.get_trades_for_wallet(address)
    patterns = patterns_to_dict(extract_patterns(trades) or _empty_patterns(address))

    ranking = repo.get_ranking_for_wallet(address)
    heuristic_flags: list[str] = []
    if ranking and ranking.heuristic_red_flags:
        try:
            heuristic_flags = json.loads(ranking.heuristic_red_flags)
        except json.JSONDecodeError:
            pass

    prompt = _build_prompt(address, metrics, patterns, heuristic_flags)

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
    # Strip common LLM noise like triple-backtick fences
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed for Claude response on %s: %s\nRaw: %s", address, exc, raw[:300])
        return None

    # Validate required fields
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


def _empty_patterns(address: str) -> Any:
    from analysis.patterns import WalletPatterns
    return WalletPatterns(wallet_address=address)
