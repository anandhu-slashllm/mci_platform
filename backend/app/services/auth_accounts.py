"""Link app users (public.users) to Supabase Auth users (auth.users).

The JWT issued by Supabase carries ``app_metadata`` (writable only with the service-role key);
we store ``mci_user_id`` and ``mci_role`` there so the frontend can route without an extra call.
The backend itself always authorises from the database row, never from these claims.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.supabase_auth import AuthServiceError, AuthSession, SupabaseAuthClient
from app.models import User

# Username-only staff/demo accounts have no real mailbox; Supabase Auth still needs an email identity.
SYNTHETIC_EMAIL_DOMAIN = "users.mci-platform.local"


def auth_email_for(user: User) -> str | None:
    if user.email and user.email.strip():
        return user.email.strip().lower()
    if user.username:
        local = re.sub(r"[^a-z0-9._-]", "-", user.username.strip().lower())
        return f"{local}@{SYNTHETIC_EMAIL_DOMAIN}"
    return None


def app_metadata_for(user: User) -> dict[str, Any]:
    return {"mci_user_id": user.id, "mci_role": user.role.value}


def _metadata_matches(auth_user: dict[str, Any], user: User) -> bool:
    meta = auth_user.get("app_metadata") or {}
    return meta.get("mci_user_id") == user.id and meta.get("mci_role") == user.role.value


async def ensure_auth_user(
    db: AsyncSession,
    client: SupabaseAuthClient,
    user: User,
    *,
    password: str | None = None,
    password_hash: str | None = None,
) -> str:
    """Create (or adopt by email) the Supabase Auth user for ``user`` and store the link.

    ``password`` sets/overwrites the password; ``password_hash`` imports an existing bcrypt hash
    (only applied when the auth user is created).
    """
    email = auth_email_for(user)
    if not email:
        raise ValueError(f"user {user.id} has neither email nor username; cannot create an auth identity")

    if user.supabase_user_id:
        attrs: dict[str, Any] = {"app_metadata": app_metadata_for(user)}
        if password:
            attrs["password"] = password
        await client.admin_update_user(user.supabase_user_id, attrs)
        return user.supabase_user_id

    body_meta = app_metadata_for(user)
    try:
        created = await client.admin_create_user(
            email=email, password=password, password_hash=password_hash, app_metadata=body_meta
        )
        uid = str(created["id"])
    except AuthServiceError as e:
        if e.status_code != 422 and e.code != "email_exists":
            raise
        existing = await client.admin_find_user_by_email(email)
        if not existing:
            raise
        uid = str(existing["id"])
        attrs = {"app_metadata": body_meta}
        if password:
            attrs["password"] = password
        await client.admin_update_user(uid, attrs)

    user.supabase_user_id = uid
    await db.flush()
    return uid


async def finalize_session(client: SupabaseAuthClient, session: AuthSession, user: User) -> AuthSession:
    """Make sure the access token carries current app_metadata; refresh once if we had to update it."""
    if _metadata_matches(session.user, user):
        return session
    await client.admin_update_user(session.user_id, {"app_metadata": app_metadata_for(user)})
    return await client.refresh(session.refresh_token)


async def sync_auth_email(client: SupabaseAuthClient, user: User, new_email: str | None) -> None:
    """Keep the Supabase login email in step with a profile email change."""
    if not user.supabase_user_id or not new_email:
        return
    try:
        await client.admin_update_user(user.supabase_user_id, {"email": new_email, "email_confirm": True})
    except AuthServiceError as e:
        if e.status_code == 422 or e.code == "email_exists":
            raise HTTPException(status_code=409, detail="Email already in use") from e
        raise HTTPException(status_code=502, detail="Could not update login email") from e
