from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from config import CLAUDE_REVIEW_TOP_N, setup_logging
from data.database import init_db
from data.schema import WalletMetrics, WalletRanking
from scanner import repository as repo
from scanner.client import PolymarketClient
from scanner.metrics import apply_hard_filters, compute_metrics, parse_trades
from scanner.ranking import rank_wallets

logger = logging.getLogger(__name__)

# Limit concurrent wallet fetches to avoid flooding the API even with rate-limiting
_CONCURRENCY = 50


async def run_scan(
    incremental: bool = False,
    max_wallets: int = 10_000,
) -> list[WalletRanking]:
    """
    Full scan pipeline:
      1. Discover/load wallet addresses
      2. Fetch trade histories (async, bounded concurrency)
      3. Compute metrics, apply hard filters
      4. Rank wallets
      5. Detect heuristic red flags
      6. Claude qualitative review on top N
      7. Persist results — all DB writes atomic

    Returns the final ranked list.
    """
    setup_logging()
    init_db()

    async with PolymarketClient() as client:
        # ── Step 1: Wallet discovery ──────────────────────────────────────
        if incremental:
            stale = repo.get_stale_wallets(older_than_hours=24)
            addresses = [w.address for w in stale]
            logger.info("Incremental mode: %d wallets to refresh", len(addresses))
        else:
            addresses = await client.get_all_traders(max_wallets=max_wallets)
            repo.upsert_wallets(addresses)
            logger.info("Full scan: %d wallet addresses", len(addresses))

        if not addresses:
            logger.warning("No wallets to process — exiting early")
            return []

        # ── Step 2: Fetch + compute metrics (bounded concurrency) ─────────
        sem = asyncio.Semaphore(_CONCURRENCY)
        all_metrics: list[WalletMetrics] = []

        async def process_wallet(address: str) -> WalletMetrics | None:
            async with sem:
                try:
                    raw_trades = await client.get_wallet_trades(address)
                    if not raw_trades:
                        return None
                    trades = parse_trades(address, raw_trades)
                    repo.upsert_trades(trades)
                    metrics = compute_metrics(trades)
                    if metrics:
                        repo.upsert_metrics(metrics)
                    repo.mark_wallet_scanned(address)
                    return metrics
                except Exception as exc:
                    # Never crash the full scan on a single bad wallet
                    logger.warning("Skipping wallet %s: %s", address, exc)
                    return None

        tasks = [process_wallet(addr) for addr in addresses]
        results = await asyncio.gather(*tasks)
        all_metrics = [r for r in results if r is not None]
        logger.info("Computed metrics for %d wallets", len(all_metrics))

        # ── Step 3: Hard filters ──────────────────────────────────────────
        filtered = apply_hard_filters(all_metrics)
        if not filtered:
            logger.warning("No wallets passed hard filters — check MIN_TRADES / MIN_WIN_RATE")
            return []

        # ── Step 4: Composite ranking ─────────────────────────────────────
        rankings = rank_wallets(filtered)
        repo.upsert_rankings(rankings)

        # ── Step 5: Heuristic red flags ───────────────────────────────────
        from analysis.red_flags import get_red_flags

        metrics_by_addr = {m.wallet_address: m for m in filtered}
        for ranking in rankings:
            metrics = metrics_by_addr.get(ranking.wallet_address)
            if metrics:
                flags = get_red_flags(metrics)
                repo.update_heuristic_flags(ranking.wallet_address, flags)

        # ── Step 6: Claude qualitative review (top N only) ────────────────
        top_n = rankings[:CLAUDE_REVIEW_TOP_N]
        if top_n:
            logger.info("Sending top %d wallets for Claude review", len(top_n))
            await _claude_review_pass(top_n, metrics_by_addr)

        logger.info("Scan complete — %d wallets ranked", len(rankings))
        return rankings


async def _claude_review_pass(
    rankings: list[WalletRanking],
    metrics_by_addr: dict[str, WalletMetrics],
) -> None:
    """Run Claude qualitative review concurrently with a conservative concurrency cap."""
    from analysis.claude_review import review_wallet

    # Claude review: 5 concurrent to stay within rate limits and cost budget
    sem = asyncio.Semaphore(5)

    async def review_one(ranking: WalletRanking) -> None:
        async with sem:
            metrics = metrics_by_addr.get(ranking.wallet_address)
            if metrics is None:
                return
            try:
                result = await review_wallet(ranking.wallet_address, metrics)
                if result:
                    repo.update_claude_review(
                        ranking.wallet_address,
                        skill_signal=result.get("skill_signal"),
                        edge_hypothesis=result.get("edge_hypothesis"),
                        red_flags=result.get("red_flags", []),
                        notes=result.get("notes"),
                    )
            except Exception as exc:
                logger.warning(
                    "Claude review failed for %s: %s", ranking.wallet_address, exc
                )

    await asyncio.gather(*[review_one(r) for r in rankings])
    logger.info("Claude review complete for %d wallets", len(rankings))
