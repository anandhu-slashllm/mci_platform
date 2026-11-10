"""In-memory stand-in for Supabase Auth with the same interface as SupabaseAuthClient.

Issues real HS256 JWTs (TEST_JWT_SECRET) so the backend's token verification runs unmodified.
Email OTP codes are always FakeSupabaseAuth.OTP_CODE.
"""

from __future__ import annotations

import copy
import secrets
import time
import uuid
from typing import Any, Optional

from app.auth.supabase_auth import AuthServiceError, AuthSession
from auth_helpers import mint_token


class FakeSupabaseAuth:
    OTP_CODE = "123456"

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.otp_sent_to: list[str] = []
        self.refresh_tokens: dict[str, str] = {}
        self.signed_out: list[str] = []

    # --- helpers for tests ---------------------------------------------------------------------

    def add_user(
        self,
        email: str,
        *,
        password: str | None = None,
        uid: str | None = None,
        app_metadata: dict[str, Any] | None = None,
    ) -> str:
        uid = uid or str(uuid.uuid4())
        self.users[uid] = {
            "id": uid,
            "email": email.lower(),
            "password": password,
            "app_metadata": dict(app_metadata or {}),
        }
        return uid

    def by_email(self, email: str) -> Optional[dict[str, Any]]:
        email = email.lower()
        return next((u for u in self.users.values() if u["email"] == email), None)

    def _public(self, u: dict[str, Any]) -> dict[str, Any]:
        return {"id": u["id"], "email": u["email"], "app_metadata": copy.deepcopy(u["app_metadata"])}

    def _session(self, u: dict[str, Any]) -> AuthSession:
        rt = secrets.token_urlsafe(16)
        self.refresh_tokens[rt] = u["id"]
        return AuthSession(
            access_token=mint_token(u["id"], copy.deepcopy(u["app_metadata"])),
            refresh_token=rt,
            expires_in=3600,
            expires_at=int(time.time()) + 3600,
            user=self._public(u),
        )

    # --- end-user flows ------------------------------------------------------------------------

    async def sign_in_with_password(self, email: str, password: str) -> AuthSession:
        u = self.by_email(email)
        if not u or not u["password"] or u["password"] != password:
            raise AuthServiceError(400, "Invalid login credentials", "invalid_credentials")
        return self._session(u)

    async def send_email_otp(self, email: str, *, create_user: bool) -> None:
        if not create_user and not self.by_email(email):
            raise AuthServiceError(422, "Signups not allowed for otp", "otp_disabled")
        self.otp_sent_to.append(email.lower())

    async def verify_email_otp(self, email: str, token: str) -> AuthSession:
        email = email.lower()
        if token != self.OTP_CODE or email not in self.otp_sent_to:
            raise AuthServiceError(403, "Token has expired or is invalid", "otp_expired")
        self.otp_sent_to.remove(email)
        u = self.by_email(email) or self.users[self.add_user(email)]
        return self._session(u)

    async def refresh(self, refresh_token: str) -> AuthSession:
        uid = self.refresh_tokens.pop(refresh_token, None)
        if uid is None or uid not in self.users:
            raise AuthServiceError(400, "Invalid Refresh Token", "refresh_token_not_found")
        return self._session(self.users[uid])

    async def sign_out(self, access_token: str) -> None:
        self.signed_out.append(access_token)

    # --- admin ---------------------------------------------------------------------------------

    async def admin_get_user(self, uid: str) -> dict[str, Any]:
        if uid not in self.users:
            raise AuthServiceError(404, "User not found", "user_not_found")
        return self._public(self.users[uid])

    async def admin_create_user(
        self,
        *,
        email: str,
        password: str | None = None,
        password_hash: str | None = None,
        app_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.by_email(email):
            raise AuthServiceError(422, "A user with this email address has already been registered", "email_exists")
        uid = self.add_user(email, password=password, app_metadata=app_metadata)
        return self._public(self.users[uid])

    async def admin_update_user(self, uid: str, attrs: dict[str, Any]) -> dict[str, Any]:
        if uid not in self.users:
            raise AuthServiceError(404, "User not found", "user_not_found")
        u = self.users[uid]
        if "email" in attrs:
            other = self.by_email(attrs["email"])
            if other and other["id"] != uid:
                raise AuthServiceError(422, "Email address already registered", "email_exists")
            u["email"] = attrs["email"].lower()
        if "password" in attrs:
            u["password"] = attrs["password"]
        if "app_metadata" in attrs:
            u["app_metadata"].update(attrs["app_metadata"])
        return self._public(u)

    async def admin_delete_user(self, uid: str) -> None:
        self.users.pop(uid, None)

    async def admin_find_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        u = self.by_email(email)
        return self._public(u) if u else None
