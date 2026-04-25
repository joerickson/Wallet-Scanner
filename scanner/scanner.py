from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from config import CLAUDE_REVIEW_TOP_N, setup_logging
from data.database import init_db, sync_to_turso
from data.schema import WalletMetrics, WalletRanking
from scanner import repository as repo
from scanner.client import PolymarketClient
from scanner.metrics import apply_hard_filters, compute_metrics, parse_trades
from scanner.ranking import rank_wallets

logger = logging.getLogger(__name__)

_CONCURRENCY = 50
_CLAUDE_REVIEW_TTL_DAYS = 7


async def run_scan(
    incremental: bool = False,
    max_wallets: int = 10_000,
) -> list[WalletRanking]:
    """
    Full scan pipeline:
      1. Decide which wallets to refresh (all, or only stale ones in incremental mode)
      2. Fetch trade histories (async, bounded concurrency)
      3. Load all metrics from DB + apply hard filters
      4. Rank ALL wallets (not just refreshed ones)
      5. Detect heuristic red flags
      6. Claude qualitative review on top N (skip fresh reviews in incremental mode)
      7. Sync writes to Turso (no-op if using local SQLite)

    Returns the final ranked list.
    """
    setup_logging()
    init_db()

    async with PolymarketClient() as client:
        # ── Step 1: Determine which wallets to fetch ──────────────────────────
        if incremental:
            stale = repo.get_stale_wallets(older_than_hours=24)
            addresses_to_refresh = [w.address for w in stale]

            if not addresses_to_refresh:
                if not repo.get_all_wallets():
                    # Empty DB — first scheduled run, fall back to full discovery
                    logger.info("Incremental: empty DB, running initial wallet discovery")
                    addresses_to_refresh = await client.get_all_traders(max_wallets=max_wallets)
                    repo.upsert_wallets(addresses_to_refresh)
                else:
                    logger.info("Incremental: all wallets are fresh, skipping fetch phase")
            else:
                logger.info("Incremental: %d stale wallets to refresh", len(addresses_to_refresh))
        else:
            addresses_to_refresh = await client.get_all_traders(max_wallets=max_wallets)
            repo.upsert_wallets(addresses_to_refresh)
            logger.info("Full scan: %d wallet addresses", len(addresses_to_refresh))

        # ── Step 2: Fetch + compute metrics (bounded concurrency) ─────────────
        if addresses_to_refresh:
            sem = asyncio.Semaphore(_CONCURRENCY)

            async def process_wallet(address: str) -> None:
                async with sem:
                    try:
                        raw_trades = await client.get_wallet_trades(address)
                        if not raw_trades:
                            return
                        trades = parse_trades(address, raw_trades)
                        repo.upsert_trades(trades)
                        metrics = compute_metrics(trades)
                        if metrics:
                            repo.upsert_metrics(metrics)
                        repo.mark_wallet_scanned(address)
                    except Exception as exc:
                        logger.warning("Skipping wallet %s: %s", address, exc)

            await asyncio.gather(*[process_wallet(addr) for addr in addresses_to_refresh])
            logger.info("Refreshed metrics for up to %d wallets", len(addresses_to_refresh))

        # ── Step 3: Load ALL metrics + apply hard filters ─────────────────────
        # Always rank the full DB, not just the wallets refreshed this run.
        all_metrics = repo.get_all_metrics()
        filtered = apply_hard_filters(all_metrics)
        if not filtered:
            logger.warning("No wallets passed hard filters — check MIN_TRADES / MIN_WIN_RATE")
            return []
        logger.info("Hard filters passed: %d wallets", len(filtered))

        # ── Step 4: Composite ranking ─────────────────────────────────────────
        rankings = rank_wallets(filtered)
        repo.upsert_rankings(rankings)

        # ── Step 5: Heuristic red flags ───────────────────────────────────────
        from analysis.red_flags import get_red_flags

        metrics_by_addr = {m.wallet_address: m for m in filtered}
        for ranking in rankings:
            metrics = metrics_by_addr.get(ranking.wallet_address)
            if metrics:
                flags = get_red_flags(metrics)
                repo.update_heuristic_flags(ranking.wallet_address, flags)

        # ── Step 6: Claude qualitative review (top N only) ────────────────────
        top_n = rankings[:CLAUDE_REVIEW_TOP_N]
        if top_n:
            if incremental:
                # Skip wallets whose Claude review is less than 7 days old
                cutoff = datetime.utcnow() - timedelta(days=_CLAUDE_REVIEW_TTL_DAYS)
                db_rankings = repo.get_rankings_for_wallets(
                    [r.wallet_address for r in top_n]
                )
                top_n = [
                    r for r in top_n
                    if db_rankings.get(r.wallet_address) is None
                    or db_rankings[r.wallet_address].reviewed_at is None
                    or db_rankings[r.wallet_address].reviewed_at < cutoff
                ]
                logger.info("Incremental: %d wallets need fresh Claude review", len(top_n))

            if top_n:
                logger.info("Sending top %d wallets for Claude review", len(top_n))
                await _claude_review_pass(top_n, metrics_by_addr)

        # ── Step 7: Sync to Turso ─────────────────────────────────────────────
        sync_to_turso()

        logger.info("Scan complete — %d wallets ranked", len(rankings))
        return rankings


async def _claude_review_pass(
    rankings: list[WalletRanking],
    metrics_by_addr: dict[str, WalletMetrics],
) -> None:
    """Run Claude qualitative review concurrently with a conservative concurrency cap."""
    from analysis.claude_review import review_wallet

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
