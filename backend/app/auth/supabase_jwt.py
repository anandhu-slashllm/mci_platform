"""Verify Supabase Auth access tokens locally (no network call per request).

Supports both signing schemes Supabase uses:
- HS256 with the project's legacy JWT secret (local CLI default; ``SUPABASE_JWT_SECRET``).
- Asymmetric keys (ES256/RS256) published at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``
  (default for new hosted projects). The key set is cached and refetched when an unknown ``kid`` appears.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError, jwt

from app.config import get_settings

AUDIENCE = "authenticated"
_JWKS_TTL_SECONDS = 600

_jwks_cache: dict[str, Any] = {"keys": [], "fetched_at": 0.0}


async def _fetch_jwks(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS:
        return _jwks_cache["keys"]
    url = get_settings().supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            keys = r.json().get("keys") or []
    except (httpx.HTTPError, ValueError) as e:
        raise JWTError(f"Could not load JWKS: {e}") from e
    _jwks_cache.update(keys=keys, fetched_at=now)
    return keys


async def decode_supabase_token(token: str) -> dict[str, Any]:
    """Return verified claims or raise ``JWTError``."""
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if alg == "HS256":
        secret = get_settings().supabase_jwt_secret
        if not secret:
            raise JWTError("SUPABASE_JWT_SECRET is not configured")
        return jwt.decode(token, secret, algorithms=["HS256"], audience=AUDIENCE)
    if alg in ("ES256", "RS256"):
        kid = header.get("kid")
        keys = await _fetch_jwks()
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            keys = await _fetch_jwks(force=True)
            key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            raise JWTError("Unknown signing key")
        return jwt.decode(token, key, algorithms=[alg], audience=AUDIENCE)
    raise JWTError(f"Unsupported JWT alg: {alg}")
