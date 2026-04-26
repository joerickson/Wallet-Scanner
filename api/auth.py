from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

STACK_PROJECT_ID = os.getenv("STACK_PROJECT_ID", "")
STACK_PUBLISHABLE_CLIENT_KEY = os.getenv("STACK_PUBLISHABLE_CLIENT_KEY", "")
STACK_SECRET_SERVER_KEY = os.getenv("STACK_SECRET_SERVER_KEY", "")
STACK_API_BASE = "https://api.stack-auth.com/api/v1"

SESSION_COOKIE = "ws_session"
_PKCE_COOKIE = "ws_pkce"
_STATE_COOKIE = "ws_state"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

# When Stack Auth env vars are absent (local dev), auth is disabled and every
# request is treated as the "local" user so the app still runs without a Neon
# Auth project configured.
AUTH_ENABLED = bool(STACK_PROJECT_ID and STACK_PUBLISHABLE_CLIENT_KEY and STACK_SECRET_SERVER_KEY)

_LOCAL_DEV_USER = {"id": "local-dev", "primary_email": "local@dev", "display_name": "Local Dev"}


def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


def _callback_url(request: Request) -> str:
    return f"{_base_url(request)}/api/auth/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def start_oauth(request: Request, provider: str) -> RedirectResponse:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)

    params = {
        "type": "oauth",
        "provider_id": provider,
        "client_id": STACK_PUBLISHABLE_CLIENT_KEY,
        "redirect_uri": _callback_url(request),
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"{STACK_API_BASE}/auth/oauth/authorize?{qs}"

    is_secure = _base_url(request).startswith("https://")
    response = RedirectResponse(auth_url, status_code=302)
    response.set_cookie(_PKCE_COOKIE, verifier, httponly=True, max_age=600, samesite="lax", secure=is_secure)
    response.set_cookie(_STATE_COOKIE, state, httponly=True, max_age=600, samesite="lax", secure=is_secure)
    return response


async def handle_callback(request: Request) -> RedirectResponse:
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(f"/login?error={error}", status_code=302)

    if not state or state != request.cookies.get(_STATE_COOKIE):
        return RedirectResponse("/login?error=state_mismatch", status_code=302)

    verifier = request.cookies.get(_PKCE_COOKIE)
    if not verifier or not code:
        return RedirectResponse("/login?error=missing_params", status_code=302)

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{STACK_API_BASE}/auth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": _callback_url(request),
                    "client_id": STACK_PUBLISHABLE_CLIENT_KEY,
                    "client_secret": STACK_SECRET_SERVER_KEY,
                },
                timeout=10.0,
            )
        except httpx.RequestError:
            return RedirectResponse("/login?error=network_error", status_code=302)

    if r.status_code != 200:
        return RedirectResponse(f"/login?error=token_exchange_failed", status_code=302)

    token_data = r.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return RedirectResponse("/login?error=no_access_token", status_code=302)

    is_secure = _base_url(request).startswith("https://")
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        access_token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=is_secure,
    )
    response.delete_cookie(_PKCE_COOKIE)
    response.delete_cookie(_STATE_COOKIE)
    return response


async def validate_session(request: Request) -> Optional[dict]:
    """Return user dict if session is valid, else None.

    When AUTH_ENABLED is False (local dev without Stack Auth keys), always
    returns the local-dev sentinel user so the app is usable without OAuth.
    """
    if not AUTH_ENABLED:
        return _LOCAL_DEV_USER

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{STACK_API_BASE}/users/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-stack-project-id": STACK_PROJECT_ID,
                    "x-stack-access-type": "server",
                    "x-stack-secret-server-key": STACK_SECRET_SERVER_KEY,
                },
                timeout=5.0,
            )
            if r.status_code == 200:
                return r.json()
        except httpx.RequestError:
            pass
    return None


async def require_auth(request: Request) -> dict:
    user = await validate_session(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "Authentication required", "login_url": "/login"},
        )
    return user


def signout_response(is_secure: bool = True) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE, secure=is_secure, httponly=True, samesite="lax")
    return response
