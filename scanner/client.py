from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import API_CACHE_TTL, API_RATE_LIMIT, POLYMARKET_DATA_API_BASE

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Token-bucket rate limiter for async code."""

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class _ResponseCache:
    """In-memory response cache with TTL."""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _key(path: str, params: dict[str, Any]) -> str:
        raw = path + json.dumps(params, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, path: str, params: dict[str, Any]) -> Any | None:
        k = self._key(path, params)
        entry = self._store.get(k)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts > self._ttl:
            del self._store[k]
            return None
        return val

    def set(self, path: str, params: dict[str, Any], value: Any) -> None:
        # Always record timestamp so callers can check freshness
        self._store[self._key(path, params)] = (time.time(), value)


class PolymarketClient:
    """
    Async HTTP client for the Polymarket Data API.

    Usage::

        async with PolymarketClient() as client:
            traders = await client.get_all_traders()
    """

    def __init__(
        self,
        base_url: str = POLYMARKET_DATA_API_BASE,
        rate_limit: float = API_RATE_LIMIT,
        cache_ttl: int = API_CACHE_TTL,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._limiter = _RateLimiter(rate_limit)
        self._cache = _ResponseCache(cache_ttl)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> PolymarketClient:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        cached = self._cache.get(path, params)
        if cached is not None:
            logger.debug("Cache hit  %s %s", path, params)
            return cached

        await self._limiter.acquire()
        assert self._client is not None, "Client used outside context manager"
        url = f"{self._base_url}{path}"
        logger.debug("GET %s params=%s", url, params)
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        self._cache.set(path, params, data)
        return data

    # ── Public helpers ─────────────────────────────────────────────────────

    async def get_leaderboard(
        self,
        window: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a page of leaderboard entries."""
        data = await self._get(
            "/leaderboard", {"window": window, "limit": limit, "offset": offset}
        )
        return _as_list(data)

    async def get_activity(
        self,
        user: str | None = None,
        limit: int = 500,
        offset: int = 0,
        activity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a page of activity records, optionally filtered by user."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if user:
            params["user"] = user
        if activity_type:
            params["type"] = activity_type
        data = await self._get("/activity", params)
        return _as_list(data)

    async def get_positions(
        self,
        user: str,
        limit: int = 500,
        offset: int = 0,
        size_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return current open positions for a wallet."""
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        if size_threshold is not None:
            params["sizeThreshold"] = size_threshold
        data = await self._get("/positions", params)
        return _as_list(data)

    async def get_all_traders(self, max_wallets: int = 10_000) -> list[str]:
        """
        Discover wallet addresses by paginating the leaderboard, then
        falling back to the activity feed if more addresses are needed.
        """
        addresses: set[str] = set()

        # Primary source: leaderboard endpoint
        offset = 0
        while len(addresses) < max_wallets:
            try:
                records = await self.get_leaderboard(limit=100, offset=offset)
            except httpx.HTTPStatusError as exc:
                logger.warning("Leaderboard error at offset=%d: %s", offset, exc)
                break
            if not records:
                break
            for r in records:
                addr = _extract_address(r)
                if addr:
                    addresses.add(addr)
            if len(records) < 100:
                break
            offset += 100

        # Fallback: mine addresses from the activity feed
        if len(addresses) < max_wallets:
            offset = 0
            while len(addresses) < max_wallets:
                try:
                    records = await self.get_activity(limit=500, offset=offset)
                except httpx.HTTPStatusError as exc:
                    logger.warning("Activity error at offset=%d: %s", offset, exc)
                    break
                if not records:
                    break
                for r in records:
                    addr = _extract_address(r)
                    if addr:
                        addresses.add(addr)
                if len(records) < 500:
                    break
                offset += 500

        result = list(addresses)[:max_wallets]
        logger.info("Discovered %d unique wallet addresses", len(result))
        return result

    async def get_wallet_trades(
        self, address: str, max_trades: int = 2_000
    ) -> list[dict[str, Any]]:
        """Fetch the full trade history for one wallet, paginating as needed."""
        trades: list[dict[str, Any]] = []
        offset = 0
        limit = 500

        while len(trades) < max_trades:
            try:
                batch = await self.get_activity(
                    user=address, limit=limit, offset=offset
                )
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Trade fetch error for %s at offset=%d: %s", address, offset, exc
                )
                break
            if not batch:
                break
            trades.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

        return trades


# ── Utilities ──────────────────────────────────────────────────────────────────

def _as_list(data: Any) -> list[dict[str, Any]]:
    """Normalise API response — handles both bare lists and envelope dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "results", "items", "leaderboard"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def _extract_address(record: dict[str, Any]) -> str | None:
    """Pull the wallet address from an activity or leaderboard record."""
    for key in ("address", "user", "maker", "trader"):
        val = record.get(key)
        if val and isinstance(val, str) and val.startswith("0x"):
            return val.lower()
    return None
