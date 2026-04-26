from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").rstrip("/")
NEON_AUTH_COOKIE_SECRET = os.getenv("NEON_AUTH_COOKIE_SECRET", "")

# Better Auth session cookie name
SESSION_COOKIE = "better-auth.session_token"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

# AUTH_ENABLED is True only when NEON_AUTH_BASE_URL is configured.
# When False (local dev), every request is treated as the "local-dev" user so
# the app runs without OAuth.
AUTH_ENABLED = bool(NEON_AUTH_BASE_URL)

_LOCAL_DEV_USER = {"id": "local-dev", "primary_email": "local@dev", "display_name": "Local Dev"}


def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


async def start_oauth(request: Request, provider: str) -> RedirectResponse:
    """Initiate social OAuth via Neon Auth (Better Auth).

    Calls Better Auth's sign-in/social endpoint to obtain the provider redirect
    URL, then sends the browser to the OAuth provider.
    """
    callback_url = f"{_base_url(request)}/api/auth/callback"

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{NEON_AUTH_BASE_URL}/api/auth/sign-in/social",
                json={"provider": provider, "callbackURL": callback_url},
                headers={"content-type": "application/json"},
                timeout=10.0,
                follow_redirects=False,
            )
        except httpx.RequestError:
            return RedirectResponse("/login?error=network_error", status_code=302)

    # Better Auth returns {"url": "https://accounts.google.com/..."} on success
    if r.status_code in (200, 201):
        data = r.json()
        redirect_url = data.get("url") or data.get("redirect")
        if redirect_url:
            return RedirectResponse(redirect_url, status_code=302)
    elif r.status_code in (301, 302, 307, 308):
        location = r.headers.get("location")
        if location:
            return RedirectResponse(location, status_code=302)

    return RedirectResponse("/login?error=auth_init_failed", status_code=302)


async def handle_callback(request: Request) -> RedirectResponse:
    """Post-OAuth landing point: validate session and forward to the dashboard.

    After Better Auth completes the OAuth flow on the Neon Auth side, it
    redirects the browser here.  We validate the session (forwarding the
    better-auth.session_token cookie to Neon Auth) and go to / on success.

    For this to work, the better-auth.session_token cookie must be readable by
    this domain.  That requires NEON_AUTH_BASE_URL to share the same origin, or
    Neon Auth's trusted-origins to be configured to set cross-origin cookies.
    """
    user = await validate_session(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return RedirectResponse("/login?error=auth_failed", status_code=302)


async def validate_session(request: Request) -> Optional[dict]:
    """Return user dict if session is valid, else None.

    When AUTH_ENABLED is False (local dev without Neon Auth configured), always
    returns the local-dev sentinel user so the app is usable without OAuth.

    Validates by forwarding the better-auth.session_token cookie (or an
    Authorization: Bearer token) to Neon Auth's GET /api/auth/get-session.
    """
    if not AUTH_ENABLED:
        return _LOCAL_DEV_USER

    token = request.cookies.get(SESSION_COOKIE)
    # Also accept Authorization: Bearer <token> for programmatic API clients
    auth_header = request.headers.get("authorization", "")
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""

    if not token and not bearer:
        return None

    headers: dict[str, str] = {}
    if token:
        headers["cookie"] = f"{SESSION_COOKIE}={token}"
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{NEON_AUTH_BASE_URL}/api/auth/get-session",
                headers=headers,
                timeout=5.0,
            )
            if r.status_code == 200:
                data = r.json()
                user = data.get("user")
                if user:
                    return {
                        "id": user.get("id"),
                        "primary_email": user.get("email"),
                        "display_name": user.get("name"),
                    }
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


async def signout_response(request: Request) -> RedirectResponse:
    """Invalidate the Neon Auth session and clear the local session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"{NEON_AUTH_BASE_URL}/api/auth/sign-out",
                    headers={"cookie": f"{SESSION_COOKIE}={token}"},
                    timeout=5.0,
                )
            except httpx.RequestError:
                pass

    is_secure = _base_url(request).startswith("https://")
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE, secure=is_secure, httponly=True, samesite="lax")
    return response
