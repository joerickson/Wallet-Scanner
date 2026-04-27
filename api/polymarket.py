"""Polymarket API client — market discovery (Gamma API) and live prices (CLOB API)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Maps paper_test_filter sport names to Polymarket tag labels.
SPORT_TAG_MAP: dict[str, list[str]] = {
    "basketball": ["Basketball", "NBA"],
    "tennis": ["Tennis"],
    "soccer": ["Soccer", "MLS", "EPL", "Champions League", "La Liga"],
    "baseball": ["Baseball", "MLB"],
    "hockey": ["Hockey", "NHL"],
    "football": ["NFL", "American Football"],
}

_client: httpx.AsyncClient | None = None
_CACHE_TTL = 60.0
# {cache_key: (monotonic_timestamp, results)}
_market_cache: dict[str, tuple[float, list]] = {}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


# ── Pydantic models ───────────────────────────────────────────────────────────


class Outcome(BaseModel):
    name: str
    token_id: str
    current_price: float | None


class Market(BaseModel):
    condition_id: str
    question: str
    slug: str
    category: str | None
    tags: list[str]
    end_date: datetime | None
    volume_usd: float
    liquidity_usd: float
    outcomes: list[Outcome]


class Orderbook(BaseModel):
    token_id: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


class PricePoint(BaseModel):
    timestamp: int
    price: float


# ── Internal helpers ──────────────────────────────────────────────────────────


def _parse_market(data: dict) -> Market | None:
    """Parse a Gamma API market dict, returning None if required fields are absent."""
    try:
        condition_id = (
            data.get("conditionId")
            or data.get("condition_id")
            or ""
        )
        if not condition_id:
            return None

        question = data.get("question") or data.get("title") or ""
        if not question:
            return None

        slug = data.get("slug") or ""

        tags_raw = data.get("tags") or []
        if isinstance(tags_raw, list):
            tags = [
                t.get("label") if isinstance(t, dict) else str(t)
                for t in tags_raw
            ]
            tags = [t for t in tags if t]
        else:
            tags = []

        category = data.get("category") or None

        end_date: datetime | None = None
        raw_date = data.get("endDate") or data.get("end_date_iso") or data.get("end_date")
        if raw_date:
            try:
                end_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        try:
            volume_usd = float(data.get("volume") or data.get("volume24hr") or 0.0)
        except (TypeError, ValueError):
            volume_usd = 0.0

        try:
            liquidity_usd = float(data.get("liquidity") or 0.0)
        except (TypeError, ValueError):
            liquidity_usd = 0.0

        outcomes: list[Outcome] = []
        for token in data.get("tokens") or data.get("outcomes") or []:
            if not isinstance(token, dict):
                continue
            token_id = token.get("token_id") or token.get("tokenId") or ""
            if not token_id:
                continue
            name = token.get("outcome") or token.get("name") or ""
            price_raw = token.get("price") or token.get("current_price")
            try:
                current_price: float | None = float(price_raw) if price_raw is not None else None
            except (TypeError, ValueError):
                current_price = None
            outcomes.append(Outcome(name=name, token_id=token_id, current_price=current_price))

        return Market(
            condition_id=condition_id,
            question=question,
            slug=slug,
            category=category,
            tags=tags,
            end_date=end_date,
            volume_usd=volume_usd,
            liquidity_usd=liquidity_usd,
            outcomes=outcomes,
        )
    except Exception:
        return None


def _cache_key(filter_dict: dict) -> str:
    return json.dumps(filter_dict, sort_keys=True)


def _parse_orderbook_levels(raw: list) -> list[tuple[float, float]]:
    levels = []
    for entry in raw:
        try:
            if isinstance(entry, dict):
                price = float(entry.get("price", 0))
                size = float(entry.get("size", 0))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                price, size = float(entry[0]), float(entry[1])
            else:
                continue
            levels.append((price, size))
        except (TypeError, ValueError):
            continue
    return levels


# ── Public API ────────────────────────────────────────────────────────────────


async def search_markets(filter: dict) -> list[Market]:
    """Return open Polymarket markets matching a paper_test_filter dict.

    Results are cached in-memory for 60 s to avoid hammering the Gamma API.
    """
    key = _cache_key(filter)
    now = time.monotonic()
    if key in _market_cache:
        ts, cached = _market_cache[key]
        if now - ts < _CACHE_TTL:
            return cached  # type: ignore[return-value]

    client = _get_client()

    params: dict[str, Any] = {"active": "true", "closed": "false", "limit": 100}

    sports = filter.get("sports") or []
    tag_labels: list[str] = []
    for sport in sports:
        if sport and sport in SPORT_TAG_MAP:
            tag_labels.extend(SPORT_TAG_MAP[sport])
    for league in filter.get("leagues") or []:
        if league and league not in tag_labels:
            tag_labels.append(league)

    if tag_labels:
        # Gamma API tag= accepts a single value; use the most specific first
        params["tag"] = tag_labels[0]

    hours_max = filter.get("hours_until_resolution_max")
    if hours_max is not None:
        cutoff = datetime.now(timezone.utc).timestamp() + float(hours_max) * 3600
        params["end_date_max"] = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    try:
        resp = await client.get(f"{GAMMA_BASE}/markets", params=params)
        resp.raise_for_status()
        raw_data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    if isinstance(raw_data, dict):
        raw_markets = raw_data.get("markets") or raw_data.get("data") or []
    elif isinstance(raw_data, list):
        raw_markets = raw_data
    else:
        raw_markets = []

    min_volume = float(filter.get("min_volume_usd") or 0.0)
    min_liquidity = float(filter.get("min_liquidity_usd") or 0.0)
    hours_min = filter.get("hours_until_resolution_min")
    now_unix = datetime.now(timezone.utc).timestamp()

    results: list[Market] = []
    for raw in raw_markets:
        if not isinstance(raw, dict):
            continue
        market = _parse_market(raw)
        if market is None:
            continue

        if min_volume and market.volume_usd < min_volume:
            continue
        if min_liquidity and market.liquidity_usd < min_liquidity:
            continue

        if market.end_date is not None and (hours_max is not None or hours_min is not None):
            hours_left = (market.end_date.timestamp() - now_unix) / 3600
            if hours_max is not None and hours_left > float(hours_max):
                continue
            if hours_min is not None and hours_left < float(hours_min):
                continue

        # Confirm tag match when we applied a tag filter
        if tag_labels:
            market_tags_lower = {t.lower() for t in market.tags}
            if not any(tl.lower() in market_tags_lower for tl in tag_labels):
                continue

        results.append(market)

    _market_cache[key] = (now, results)
    return results


async def get_market(condition_id: str) -> Market:
    """Fetch a single market by condition_id from the Gamma API."""
    client = _get_client()
    resp = await client.get(f"{GAMMA_BASE}/markets", params={"condition_id": condition_id})
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list) and data:
        raw = data[0]
    elif isinstance(data, dict):
        raw = data.get("markets", [data])[0] if "markets" in data else data
    else:
        raise ValueError(f"Market not found: {condition_id}")

    market = _parse_market(raw)
    if market is None:
        raise ValueError(f"Failed to parse market: {condition_id}")
    return market


async def get_orderbook(token_id: str) -> Orderbook:
    """Fetch live order book for a market outcome from the CLOB API."""
    client = _get_client()
    resp = await client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    resp.raise_for_status()
    data = resp.json()

    bids = _parse_orderbook_levels(data.get("bids") or [])
    asks = _parse_orderbook_levels(data.get("asks") or [])
    return Orderbook(token_id=token_id, bids=bids, asks=asks)


async def get_price(token_id: str) -> float:
    """Midpoint of best bid/ask, in [0, 1]."""
    book = await get_orderbook(token_id)
    best_bid = max((b[0] for b in book.bids), default=None)
    best_ask = min((a[0] for a in book.asks), default=None)
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    if best_bid is not None:
        return best_bid
    if best_ask is not None:
        return best_ask
    return 0.5


async def get_price_history(
    token_id: str,
    start_ts: int,
    end_ts: int,
    interval: str = "1m",
) -> list[PricePoint]:
    """Historical price series for a market outcome from the CLOB API."""
    interval_to_fidelity = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}
    fidelity = interval_to_fidelity.get(interval, 1)

    client = _get_client()
    params: dict[str, Any] = {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity,
    }

    try:
        resp = await client.get(f"{CLOB_BASE}/prices-history", params=params)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    if isinstance(data, list):
        raw_history = data
    else:
        raw_history = data.get("history") or []

    points: list[PricePoint] = []
    for entry in raw_history:
        try:
            if isinstance(entry, dict):
                ts = int(entry.get("t") or entry.get("timestamp") or 0)
                price = float(entry.get("p") or entry.get("price") or 0)
            else:
                continue
            if ts:
                points.append(PricePoint(timestamp=ts, price=price))
        except (TypeError, ValueError):
            continue

    return points
