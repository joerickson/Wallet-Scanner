from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config import DISCORD_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from data.schema import Alert
from scanner import repository as repo

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class AlertEvent:
    wallet_address: str
    alert_type: str  # "new_position" | "closed_position" | "large_position"
    market_id: str
    market_question: str | None
    side: str | None
    size: float | None
    price: float | None
    details: dict | None = None


def format_alert(event: AlertEvent) -> str:
    """Build a compact one-line alert string."""
    price_str = f" @ {event.price:.2f}" if event.price is not None else ""
    size_str = f"${event.size:,.0f}" if event.size is not None else "?"
    question = (event.market_question or event.market_id)[:60]
    addr_short = f"{event.wallet_address[:6]}…{event.wallet_address[-4:]}"
    return (
        f"[{event.alert_type.upper()}] {addr_short} "
        f"{event.side or ''} {size_str}{price_str} | {question}"
    )


def display_alert(event: AlertEvent) -> None:
    """Print a rich-formatted alert panel to the terminal."""
    colour = {
        "new_position": "green",
        "closed_position": "yellow",
        "large_position": "bold magenta",
    }.get(event.alert_type, "cyan")

    addr_short = f"{event.wallet_address[:6]}…{event.wallet_address[-4:]}"
    price_str = f"{event.price:.3f}" if event.price is not None else "–"
    size_str = f"${event.size:,.2f}" if event.size is not None else "–"

    body = Text()
    body.append(f"Wallet:  ", style="dim")
    body.append(f"{addr_short}\n", style="bold")
    body.append(f"Type:    ", style="dim")
    body.append(f"{event.alert_type}\n", style=colour)
    body.append(f"Market:  ", style="dim")
    body.append(f"{(event.market_question or event.market_id)[:80]}\n")
    body.append(f"Side:    ", style="dim")
    body.append(f"{event.side or '–'}  ", style="bold")
    body.append(f"Size: {size_str}  Price: {price_str}\n")

    console.print(
        Panel(body, title=f"[{colour}]{event.alert_type.upper()}[/{colour}]", border_style=colour)
    )


def save_and_display(event: AlertEvent) -> None:
    """Persist the alert to the DB and display it in the terminal."""
    alert = Alert(
        wallet_address=event.wallet_address,
        alert_type=event.alert_type,
        market_id=event.market_id,
        market_question=event.market_question,
        side=event.side,
        size=event.size,
        price=event.price,
        details=json.dumps(event.details) if event.details else None,
        alerted_at=datetime.utcnow(),
    )
    repo.save_alert(alert)
    display_alert(event)


async def send_discord_webhook(event: AlertEvent) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    payload = {"content": format_alert(event)}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(DISCORD_WEBHOOK_URL, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Discord webhook failed: %s", exc)


async def send_telegram_message(event: AlertEvent) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": format_alert(event)}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Telegram webhook failed: %s", exc)


async def dispatch_alert(event: AlertEvent) -> None:
    """Save locally, display in terminal, and send any configured webhooks."""
    save_and_display(event)
    await send_discord_webhook(event)
    await send_telegram_message(event)
