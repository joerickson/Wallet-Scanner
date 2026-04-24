from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from config import POLL_INTERVAL, setup_logging
from data.database import init_db
from scanner import repository as repo
from scanner.client import PolymarketClient
from watch.alerter import AlertEvent, dispatch_alert

logger = logging.getLogger(__name__)


def _position_key(pos: dict) -> str:
    """Stable identifier for a position: market + outcome."""
    return f"{pos.get('market') or pos.get('conditionId', '')}:{pos.get('outcome', '')}"


def _diff_positions(
    old_json: str | None, new_positions: list[dict]
) -> tuple[list[dict], list[dict]]:
    """
    Compare stored positions with freshly fetched ones.
    Returns (new_entries, closed_entries).
    """
    old_positions: list[dict] = []
    if old_json:
        try:
            old_positions = json.loads(old_json)
        except json.JSONDecodeError:
            old_positions = []

    old_keys = {_position_key(p) for p in old_positions}
    new_keys = {_position_key(p) for p in new_positions}

    opened = [p for p in new_positions if _position_key(p) not in old_keys]
    closed = [p for p in old_positions if _position_key(p) not in new_keys]
    return opened, closed


async def poll_once(client: PolymarketClient) -> None:
    """Single poll pass over all watched wallets."""
    watched = repo.get_watched_wallets()
    if not watched:
        logger.debug("No watched wallets to poll")
        return

    for watched_wallet in watched:
        address = watched_wallet.wallet_address
        try:
            new_positions = await client.get_positions(user=address, size_threshold=1.0)
            opened, closed = _diff_positions(watched_wallet.known_positions, new_positions)

            for pos in opened:
                size = pos.get("size") or pos.get("usdcSize") or 0
                price = pos.get("avgPrice") or pos.get("price") or 0
                event = AlertEvent(
                    wallet_address=address,
                    alert_type="large_position" if float(size or 0) > 1_000 else "new_position",
                    market_id=str(pos.get("market") or pos.get("conditionId") or ""),
                    market_question=pos.get("title") or pos.get("question"),
                    side="BUY",
                    size=float(size) if size else None,
                    price=float(price) if price else None,
                    details=pos,
                )
                await dispatch_alert(event)

            for pos in closed:
                event = AlertEvent(
                    wallet_address=address,
                    alert_type="closed_position",
                    market_id=str(pos.get("market") or pos.get("conditionId") or ""),
                    market_question=pos.get("title") or pos.get("question"),
                    side=None,
                    size=None,
                    price=None,
                )
                await dispatch_alert(event)

            # Always update stored snapshot even if no diff — updates timestamp
            repo.update_watched_positions(address, json.dumps(new_positions))

        except Exception as exc:
            logger.warning("Poll error for %s: %s", address, exc)


async def run_poll_loop(interval: int = POLL_INTERVAL) -> None:
    """
    Long-running async loop that polls watched wallets every `interval` seconds.
    Intended to run until interrupted by the user (Ctrl-C).
    """
    setup_logging()
    init_db()

    from rich.console import Console

    console = Console()
    console.print(f"[green]Watching {len(repo.get_watched_wallets())} wallets — polling every {interval}s[/green]")
    console.print("[dim]Press Ctrl-C to stop[/dim]")

    async with PolymarketClient() as client:
        while True:
            poll_start = datetime.utcnow()
            try:
                await poll_once(client)
            except Exception as exc:
                logger.error("Unexpected poll error: %s", exc)

            elapsed = (datetime.utcnow() - poll_start).total_seconds()
            sleep_for = max(0, interval - elapsed)
            logger.debug("Poll cycle %.1fs — sleeping %.1fs", elapsed, sleep_for)
            await asyncio.sleep(sleep_for)
