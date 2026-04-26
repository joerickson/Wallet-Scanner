from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request

NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").rstrip("/")
AUTH_ENABLED = bool(NEON_AUTH_BASE_URL)

_LOCAL_DEV_USER = {"id": "local-dev", "email": "local@dev", "name": "Local Dev"}

_jwks_client: object = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None and AUTH_ENABLED:
        from jwt import PyJWKClient  # type: ignore

        _jwks_client = PyJWKClient(f"{NEON_AUTH_BASE_URL}/.well-known/jwks.json")
    return _jwks_client


async def validate_session(request: Request) -> Optional[dict]:
    """Return user dict if JWT is valid, else None.

    In local dev (AUTH_ENABLED=False), always returns the local-dev sentinel so
    the app is usable without Neon Auth configured.

    Validates the JWT from the Authorization: Bearer <token> header by verifying
    the signature against Neon Auth's JWKS endpoint — no proxying of requests.
    """
    if not AUTH_ENABLED:
        return _LOCAL_DEV_USER

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None

    try:
        import jwt  # type: ignore

        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["ES256", "RS256"],
            issuer=NEON_AUTH_BASE_URL,
        )
        return {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
        }
    except Exception:
        return None


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: validates JWT and returns user dict, or raises 401."""
    user = await validate_session(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "Authentication required"},
        )
    return user
