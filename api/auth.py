from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

import httpx
from fastapi import HTTPException, Request

NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").rstrip("/")
AUTH_ENABLED = bool(NEON_AUTH_BASE_URL)

_LOCAL_DEV_USER = {"id": "local-dev", "email": "local@dev", "name": "Local Dev", "role": "user"}

# In-memory session cache: SHA256(token) -> (user_dict, expiry_timestamp)
_session_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 60.0


async def validate_session(request: Request) -> Optional[dict]:
    """Return user dict if session token is valid, else None.

    In local dev (AUTH_ENABLED=False), always returns the local-dev sentinel so
    the app is usable without Neon Auth configured.

    Validates the opaque session token from the Authorization: Bearer <token> header
    by proxying to Neon Auth's /get-session endpoint. Results are cached for 60 s
    (keyed by SHA256 of the token) to avoid hammering Neon on every request.
    """
    if not AUTH_ENABLED:
        return _LOCAL_DEV_USER

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None

    cache_key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    cached = _session_cache.get(cache_key)
    if cached is not None:
        user_dict, expiry = cached
        if now < expiry:
            return user_dict
        del _session_cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{NEON_AUTH_BASE_URL}/get-session",
                headers={"Cookie": f"__Secure-neon-auth.session_token={token}"},
            )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    data = response.json()
    user_data = data.get("user")
    if not user_data:
        return None

    user = {
        "id": user_data.get("id"),
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "role": user_data.get("role", "user"),
    }
    _session_cache[cache_key] = (user, now + _CACHE_TTL)
    return user


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: validates session token and returns user dict, or raises 401."""
    user = await validate_session(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "Authentication required"},
        )
    return user
