from __future__ import annotations

import logging
import os
from typing import Optional

import jwt
from jwt import PyJWKClient, PyJWKClientError
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").rstrip("/")
AUTH_ENABLED = bool(NEON_AUTH_BASE_URL)

_LOCAL_DEV_USER = {"id": "local-dev", "email": "local@dev", "name": "Local Dev", "role": "user"}

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(f"{NEON_AUTH_BASE_URL}/.well-known/jwks.json")
    return _jwks_client


async def validate_session(request: Request) -> Optional[dict]:
    if not AUTH_ENABLED:
        return _LOCAL_DEV_USER

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256", "HS256"],
        )
    except Exception:
        raise HTTPException(status_code=401, detail={"error": "Authentication required"})

    return {
        "id": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("role", "user"),
    }


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: validates JWT and returns user dict, or raises 401."""
    user = await validate_session(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "Authentication required"},
        )
    return user
