from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import sys
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

# Leaderboard API constraints
_LEADERBOARD_MAX_LIMIT = 50
_LEADERBOARD_MAX_OFFSET = 1000

# Sweep dimensions for get_all_traders
_SWEEP_TIME_PERIODS = ["ALL", "MONTH", "WEEK"]
_SWEEP_CATEGORIES = ["OVERALL", "POLITICS", "SPORTS", "CRYPTO", "ECONOMICS", "TECH", "FINANCE"]
_SWEEP_ORDER_BY = ["PNL", "VOL"]


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
            addresses, lb_data = await client.get_all_traders_with_data()
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
        time_period: str = "ALL",
        limit: int = _LEADERBOARD_MAX_LIMIT,
        offset: int = 0,
        order_by: str = "PNL",
        category: str = "OVERALL",
    ) -> list[dict[str, Any]]:
        """Return a page of leaderboard entries."""
        data = await self._get(
            "/v1/leaderboard",
            {
                "timePeriod": time_period,
                "limit": limit,
                "offset": offset,
                "orderBy": order_by,
                "category": category,
            },
        )
        return _as_list(data)

    async def get_activity(
        self,
        user: str,
        limit: int = 500,
        offset: int = 0,
        activity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return activity records for a wallet (used by the watch/alert system)."""
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
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
        """Return a page of positions for a wallet."""
        params: dict[str, Any] = {"user": user, "limit": limit, "offset": offset}
        if size_threshold is not None:
            params["sizeThreshold"] = size_threshold
        data = await self._get("/positions", params)
        return _as_list(data)

    async def get_wallet_positions(
        self, address: str, max_positions: int = 200
    ) -> list[dict[str, Any]]:
        """Fetch all positions for one wallet, paginating as needed."""
        positions: list[dict[str, Any]] = []
        offset = 0
        limit = 100

        while len(positions) < max_positions:
            try:
                batch = await self.get_positions(user=address, limit=limit, offset=offset)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Positions fetch error for %s at offset=%d: %s", address, offset, exc
                )
                break
            if not batch:
                break
            positions.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

        return positions[:max_positions]

    async def get_wallet_value(self, address: str) -> float | None:
        """Return the current portfolio USDC value for a wallet, or None on error."""
        try:
            data = await self._get("/value", {"user": address})
        except httpx.HTTPStatusError as exc:
            logger.warning("Value fetch error for %s: %s", address, exc)
            return None
        if isinstance(data, dict):
            raw = data.get("value")
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        return None

    async def get_all_traders_with_data(
        self, max_wallets: int = 10_000
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        """
        Discover wallet addresses by sweeping the leaderboard across all
        timePeriod × category × orderBy combinations, paginating each slice
        up to offset=1000, then deduping by proxyWallet.

        Returns a tuple of:
        - list of unique wallet addresses (up to max_wallets)
        - dict mapping address → {pnl, vol} from the leaderboard (first-seen wins,
          so ALL-time data takes priority since it is swept first)

        Realistic yield: 2,000–4,000 unique wallets after deduplication.
        """
        addresses: set[str] = set()
        leaderboard_data: dict[str, dict[str, Any]] = {}

        combinations = list(
            itertools.product(_SWEEP_TIME_PERIODS, _SWEEP_CATEGORIES, _SWEEP_ORDER_BY)
        )

        for time_period, category, order_by in combinations:
            offset = 0
            while offset <= _LEADERBOARD_MAX_OFFSET:
                try:
                    records = await self.get_leaderboard(
                        time_period=time_period,
                        category=category,
                        order_by=order_by,
                        limit=_LEADERBOARD_MAX_LIMIT,
                        offset=offset,
                    )
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "Leaderboard error %s/%s/%s offset=%d: %s",
                        time_period, category, order_by, offset, exc,
                    )
                    break
                if not records:
                    break
                for r in records:
                    addr = _extract_address(r)
                    if addr:
                        addresses.add(addr)
                        # First-seen wins — ALL time period is swept first
                        if addr not in leaderboard_data:
                            leaderboard_data[addr] = {
                                "pnl": _safe_float(r.get("pnl")),
                                "vol": _safe_float(r.get("vol")),
                            }
                if len(records) < _LEADERBOARD_MAX_LIMIT:
                    break
                offset += _LEADERBOARD_MAX_LIMIT

            if len(addresses) >= max_wallets:
                break

        result = list(addresses)[:max_wallets]
        logger.info("Discovered %d unique wallet addresses", len(result))
        return result, leaderboard_data

    async def get_all_traders(self, max_wallets: int = 10_000) -> list[str]:
        """Discover wallet addresses. Returns addresses only (no leaderboard data)."""
        addresses, _ = await self.get_all_traders_with_data(max_wallets=max_wallets)
        return addresses


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
    """Pull the wallet address from a leaderboard or activity record."""
    for key in ("proxyWallet", "address", "user", "maker", "trader"):
        val = record.get(key)
        if val and isinstance(val, str) and val.startswith("0x"):
            return val.lower()
    return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Smoke test ─────────────────────────────────────────────────────────────────

async def _smoketest() -> None:
    """Sanity-check the confirmed endpoints — prints PASS/FAIL per endpoint."""
    base = POLYMARKET_DATA_API_BASE.rstrip("/")
    sample_wallet = "0x0000000000000000000000000000000000000000"

    checks: list[tuple[str, dict[str, Any]]] = [
        ("/v1/leaderboard", {"timePeriod": "ALL", "limit": 1, "offset": 0, "orderBy": "PNL", "category": "OVERALL"}),
        ("/positions", {"user": sample_wallet, "limit": 1}),
        ("/value", {"user": sample_wallet}),
    ]

    async with httpx.AsyncClient(timeout=15.0, headers={"Accept": "application/json"}) as client:
        for path, params in checks:
            url = base + path
            try:
                r = await client.get(url, params=params)
                status = r.status_code
                ok = status == 200
            except Exception as exc:
                status = str(exc)
                ok = False
            label = "PASS" if ok else "FAIL"
            print(f"{label}  {path}  (HTTP {status})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoketest":
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(_smoketest())
