from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_INPUT_COST_PER_1M,
    CLAUDE_OUTPUT_COST_PER_1M,
    CLAUDE_SCANNER_MODEL,
    STRATEGY_ANALYSIS_MAX_POSITIONS,
    STRATEGY_ANALYSIS_MAX_TOKENS,
)
from data.schema import ClaudeUsageLog, WalletStrategyAnalysis
from scanner import repository as repo

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v2"

_VALID_STRATEGY_TYPES = frozenset({
    "arbitrage", "model_driven", "discretionary", "momentum",
    "contrarian", "vig_capture", "hedging", "information_asymmetry",
    "hybrid", "unknown",
})

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _build_strategy_prompt(
    address: str,
    metrics_snapshot: dict,
    top_positions: list,
    prior_notes: str | None,
) -> str:
    total_pnl = metrics_snapshot.get("total_pnl")
    total_volume = metrics_snapshot.get("total_volume")
    market_count = metrics_snapshot.get("market_count", 0)
    realized_count = metrics_snapshot.get("realized_position_count", 0)
    pct_top3 = metrics_snapshot.get("pct_pnl_from_top_3_positions")
    composite_score = metrics_snapshot.get("composite_score")

    lines = [
        "You are analyzing a Polymarket wallet to determine if its trading strategy is replicable.",
        "Your goal is to produce a paper-tradeable specification — concrete enough that someone",
        "could read your analysis and start replicating the strategy next week with $10,000.",
        "",
        "WALLET CONTEXT:",
        f"Address: {address}",
        f"Total P&L: ${total_pnl:,.2f}" if total_pnl is not None else "Total P&L: N/A",
        f"Total volume: ${total_volume:,.2f}" if total_volume is not None else "Total volume: N/A",
        f"Distinct markets traded: {market_count}",
        f"Positions resolved: {realized_count}",
        f"Top-3 position concentration: {pct_top3:.1%}" if pct_top3 is not None else "Top-3 position concentration: N/A",
        f"Composite skill score (from earlier analysis): {composite_score:.4f}" if composite_score is not None else "Composite skill score: N/A",
        "",
        f"TOP {len(top_positions)} POSITIONS BY P&L IMPACT:",
        "[title | outcome | avg_price | current_price | size_usd | cash_pnl | hold_days | resolved]",
    ]

    for i, pos in enumerate(top_positions, start=1):
        title = (pos.title or pos.condition_id or "?")[:70]
        outcome = pos.outcome or "?"
        avg_price = f"{pos.avg_price:.3f}" if pos.avg_price is not None else "?"
        curr_price = f"{pos.current_price:.3f}" if pos.current_price is not None else "?"
        size = f"${pos.size:,.0f}" if pos.size is not None else "?"
        pnl = f"${pos.cash_pnl:,.2f}" if pos.cash_pnl is not None else "?"

        hold_days = "?"
        if pos.first_seen_at and pos.last_seen_at:
            delta = pos.last_seen_at - pos.first_seen_at
            hold_days = f"{delta.days + delta.seconds / 86400:.1f}d"

        resolved = "RESOLVED" if pos.redeemable else "open"
        lines.append(f"{i}. \"{title}\" | {outcome} | entry={avg_price} | exit={curr_price} | size={size} | pnl={pnl} | hold={hold_days} | {resolved}")

    if prior_notes:
        lines += [
            "",
            "PRIOR ANALYSIS (one-paragraph summary):",
            prior_notes,
        ]

    lines += [
        "",
        "YOUR TASK:",
        "",
        "Analyze this wallet's strategy with the rigor of a quantitative researcher.",
        "Be skeptical — many 'skilled' wallets just got lucky on a few outcomes. Distinguish between:",
        "- GENUINE EDGE: systematic exploitation of a market inefficiency",
        "- INFORMATION ADVANTAGE: knew something the market didn't (often non-replicable)",
        "- LUCK: a few large outcomes pulled their average up",
        "- INFRASTRUCTURE EDGE: faster execution, better data feeds, custom models",
        "",
        "Output a structured JSON response with EXACTLY these fields (no extra keys, no prose):",
        "",
        '{"is_replicable": bool,',
        ' "replicability_confidence": float 0.0-1.0,',
        ' "capital_required_min_usd": int or null,',
        ' "strategy_type": "arbitrage|model_driven|discretionary|momentum|contrarian|vig_capture|hedging|information_asymmetry|hybrid|unknown",',
        ' "strategy_subtype": "string or null",',
        ' "entry_signal": "string — observable, specific trigger",',
        ' "exit_signal": "string — observable, specific trigger",',
        ' "position_sizing_rule": "string — how big are bets relative to bankroll",',
        ' "market_selection_criteria": "string — which markets does this apply to",',
        ' "infrastructure_required": "string — manual, scripted, real-time feed, custom model",',
        ' "estimated_hit_rate": float 0.0-1.0 or null,',
        ' "estimated_avg_hold_time_hours": float or null,',
        ' "estimated_sharpe_proxy": float or null,',
        ' "failure_modes": ["string", ...],',
        ' "risk_factors": ["string", ...],',
        ' "full_thesis": "200-500 word complete reasoning",',
        ' "paper_trade_recommendation": "Over the next 7 days, take positions matching <criteria>...",',
        ' "paper_test_filter": {',
        '   "sports": [<"basketball"|"tennis"|"soccer"|"baseball"|"hockey"|"football"> or null if sport cannot be inferred],',
        '   "leagues": [<string> e.g. "NBA", "EPL" — empty array means any league],',
        '   "market_types": [<"binary"|"multi-outcome">],',
        '   "status": "open" or "any",',
        '   "hours_until_resolution_min": <number or null>,',
        '   "hours_until_resolution_max": <number or null>,',
        '   "min_volume_usd": <number or null>,',
        '   "min_liquidity_usd": <number or null>,',
        '   "entry_conditions": [{"type": <"combined_cost_below"|"single_side_discount_below"|"spread_above"|"custom">, "value": <number or null>, "description": <string>}],',
        '   "exit_conditions": [{"type": <"price_move_pct_in_favor"|"hedge_ratio_suboptimal"|"resolution"|"time_in_position_hours"|"custom">, "value": <number or null>, "description": <string>}],',
        '   "position_sizing": {"type": <"pct_of_capital"|"fixed_usd">, "value_min": <number>, "value_max": <number>},',
        '   "duration_days": <number — typically 7>',
        ' }',
        '}',
        "",
        "RULES FOR paper_test_filter: Base every field on the same reasoning as paper_trade_recommendation.",
        "If the prose says 'monitor NBA and tennis markets opening within 2-6 hours', then",
        "sports=[\"basketball\",\"tennis\"], leagues=[\"NBA\"], hours_until_resolution_min=2, hours_until_resolution_max=6.",
        "If a value cannot be directly inferred from the analysis, set it to null. Do not invent values.",
        "",
        "RULES FOR entry_signal and exit_signal: Be concrete.",
        "'Sentiment is positive' is WRONG.",
        "'When NBA spread on Polymarket diverges by >2.5% from DraftKings closing line for a market closing in <90 minutes' is RIGHT.",
        "",
        "IMPORTANT: If the data doesn't support confident analysis (fewer than 30 resolved trades,",
        "or all P&L from 2-3 positions), set is_replicable=false and replicability_confidence low.",
        "Explain why in full_thesis.",
    ]

    return "\n".join(lines)


def _parse_strategy_response(raw: str, address: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed for strategy response on %s: %s\nRaw: %.300s", address, exc, raw)
        return None

    required = {
        "is_replicable", "replicability_confidence", "strategy_type",
        "entry_signal", "exit_signal", "position_sizing_rule",
        "market_selection_criteria", "infrastructure_required",
        "failure_modes", "risk_factors", "full_thesis", "paper_trade_recommendation",
    }
    missing = required - set(data.keys())
    if missing:
        logger.warning("Strategy response for %s missing fields: %s", address, missing)
        return None

    if data.get("strategy_type") not in _VALID_STRATEGY_TYPES:
        logger.warning("Invalid strategy_type '%s' for %s — defaulting to unknown", data.get("strategy_type"), address)
        data["strategy_type"] = "unknown"

    conf = data.get("replicability_confidence", 0.0)
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        data["replicability_confidence"] = 0.0

    if not isinstance(data.get("failure_modes"), list):
        data["failure_modes"] = []
    if not isinstance(data.get("risk_factors"), list):
        data["risk_factors"] = []

    if not isinstance(data.get("paper_test_filter"), dict):
        data["paper_test_filter"] = None

    return data


async def analyze_wallet_strategy(
    wallet_address: str,
    top_n_positions: int = STRATEGY_ANALYSIS_MAX_POSITIONS,
) -> WalletStrategyAnalysis | None:
    """
    Run deep Claude strategy analysis for a single wallet.
    Returns a populated WalletStrategyAnalysis (not yet persisted) or None on failure.
    """
    metrics = repo.get_metrics_for_wallet(wallet_address)
    if metrics is None:
        logger.warning("No metrics for %s — skipping strategy analysis", wallet_address)
        return None

    ranking = repo.get_ranking_for_wallet(wallet_address)
    prior_notes = ranking.claude_notes if ranking else None
    composite_score = ranking.composite_score if ranking else None

    positions = repo.get_positions_for_wallet(wallet_address)
    top_positions = sorted(
        [p for p in positions if p.cash_pnl is not None],
        key=lambda p: abs(p.cash_pnl),  # type: ignore[arg-type]
        reverse=True,
    )[:top_n_positions]

    metrics_snapshot = {
        "total_pnl": metrics.total_pnl,
        "total_volume": metrics.total_volume,
        "market_count": metrics.market_count,
        "realized_position_count": metrics.realized_position_count,
        "pct_pnl_from_top_3_positions": metrics.pct_pnl_from_top_3_positions,
        "composite_score": composite_score,
        "computed_at": metrics.computed_at.isoformat(),
    }

    prompt = _build_strategy_prompt(wallet_address, metrics_snapshot, top_positions, prior_notes)

    raw: str | None = None
    input_tokens = 0
    output_tokens = 0

    try:
        client = _get_client()
        message = await client.messages.create(
            model=CLAUDE_SCANNER_MODEL,
            max_tokens=STRATEGY_ANALYSIS_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
    except anthropic.APIError as exc:
        logger.warning("Claude API error during strategy analysis for %s: %s", wallet_address, exc)
        return None
    finally:
        if input_tokens or output_tokens:
            cost = (input_tokens * CLAUDE_INPUT_COST_PER_1M / 1_000_000) + (output_tokens * CLAUDE_OUTPUT_COST_PER_1M / 1_000_000)
            logger.info(
                "Strategy analysis for %s: input=%d output=%d cost=$%.4f",
                wallet_address, input_tokens, output_tokens, cost,
            )
            repo.log_claude_usage(ClaudeUsageLog(
                call_type="strategy_analysis",
                wallet_address=wallet_address,
                model_used=CLAUDE_SCANNER_MODEL,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            ))

    data = _parse_strategy_response(raw, wallet_address)
    if data is None:
        # Retry once
        logger.info("Retrying strategy analysis parse for %s", wallet_address)
        data = _parse_strategy_response(raw, wallet_address)
        if data is None:
            logger.error("Strategy analysis parse failed twice for %s — skipping", wallet_address)
            return None

    return WalletStrategyAnalysis(
        wallet_address=wallet_address,
        is_replicable=bool(data["is_replicable"]),
        replicability_confidence=float(data["replicability_confidence"]),
        capital_required_min_usd=data.get("capital_required_min_usd"),
        strategy_type=str(data["strategy_type"]),
        strategy_subtype=data.get("strategy_subtype"),
        entry_signal=str(data["entry_signal"]),
        exit_signal=str(data["exit_signal"]),
        position_sizing_rule=str(data["position_sizing_rule"]),
        market_selection_criteria=str(data["market_selection_criteria"]),
        infrastructure_required=str(data["infrastructure_required"]),
        estimated_hit_rate=data.get("estimated_hit_rate"),
        estimated_avg_hold_time_hours=data.get("estimated_avg_hold_time_hours"),
        estimated_sharpe_proxy=data.get("estimated_sharpe_proxy"),
        failure_modes=json.dumps([str(f) for f in data.get("failure_modes", [])]),
        risk_factors=json.dumps([str(r) for r in data.get("risk_factors", [])]),
        prompt_version=PROMPT_VERSION,
        model_used=CLAUDE_SCANNER_MODEL,
        generated_at=datetime.utcnow(),
        wallet_state_snapshot=json.dumps(metrics_snapshot),
        full_thesis=str(data["full_thesis"]),
        paper_trade_recommendation=str(data["paper_trade_recommendation"]),
        paper_test_filter=json.dumps(data["paper_test_filter"]) if data.get("paper_test_filter") is not None else None,
    )
