"""Test helpers: Supabase-shaped access tokens signed with the test HS256 secret."""

from __future__ import annotations

import time
import uuid
from typing import Any

from jose import jwt

TEST_JWT_SECRET = "test-supabase-jwt-secret-with-at-least-32-chars"
_NS = uuid.UUID("6f1c7d1e-2a8b-4b7e-9a51-6a0f3c0b7c11")


def auth_uid_for_user(user_id: int) -> str:
    """Deterministic Supabase auth id for an app user id (matches the conftest insert hook)."""
    return str(uuid.uuid5(_NS, f"mci-user-{int(user_id)}"))


def mint_token(
    sub: str,
    app_metadata: dict[str, Any] | None = None,
    *,
    expires_in: int = 3600,
    secret: str = TEST_JWT_SECRET,
    audience: str = "authenticated",
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "aud": audience,
        "role": "authenticated",
        "iat": now,
        "exp": now + expires_in,
        "app_metadata": app_metadata or {},
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def create_access_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    """Drop-in for the old app-issued token helper: a Supabase token for app user ``user_id``."""
    uid = int(user_id)
    return mint_token(auth_uid_for_user(uid), {"mci_user_id": uid, **(claims or {})})
