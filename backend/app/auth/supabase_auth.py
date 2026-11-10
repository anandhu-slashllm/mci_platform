"""Server-side client for Supabase Auth (GoTrue).

All sign-in, OTP and refresh traffic goes through the backend so the frontend never needs Supabase keys,
and the backend can enforce app rules (login_only, role checks, inactive/blacklisted workers) before a
session is handed out.

Tests replace the client with an in-memory fake via ``set_auth_client`` (see tests/supabase_fake.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class AuthServiceError(Exception):
    """GoTrue returned an error (bad credentials, bad OTP, rate limit, ...)."""

    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


@dataclass
class AuthSession:
    access_token: str
    refresh_token: str
    expires_in: int
    expires_at: int
    user: dict[str, Any]

    @property
    def user_id(self) -> str:
        return str(self.user["id"])

    @property
    def email(self) -> str | None:
        return self.user.get("email")

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "AuthSession":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in=int(data.get("expires_in") or 3600),
            expires_at=int(data.get("expires_at") or 0),
            user=data.get("user") or {},
        )


class SupabaseAuthClient:
    def __init__(self, base_url: str, anon_key: str, service_role_key: str, timeout: float = 15.0):
        self.base = base_url.rstrip("/") + "/auth/v1"
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        self.timeout = timeout

    # --- plumbing ------------------------------------------------------------------------------

    def _headers(self, *, admin: bool = False, bearer: str | None = None) -> dict[str, str]:
        key = self.service_role_key if admin else self.anon_key
        if not key:
            which = "SUPABASE_SERVICE_ROLE_KEY" if admin else "SUPABASE_ANON_KEY"
            raise AuthServiceError(503, f"{which} is not configured", "not_configured")
        return {
            "apikey": key,
            "Authorization": f"Bearer {bearer or key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        admin: bool = False,
        bearer: str | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(
                    method,
                    f"{self.base}{path}",
                    headers=self._headers(admin=admin, bearer=bearer),
                    json=json,
                    params=params,
                )
        except httpx.RequestError as e:
            logger.warning("Supabase Auth unreachable: %s", e)
            raise AuthServiceError(503, "Authentication service is unavailable", "unreachable") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            msg = body.get("msg") or body.get("message") or body.get("error_description") or body.get("error") or r.text
            code = body.get("error_code") or body.get("code") or body.get("error")
            raise AuthServiceError(r.status_code, str(msg), str(code) if code else None)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # --- end-user flows ------------------------------------------------------------------------

    async def sign_in_with_password(self, email: str, password: str) -> AuthSession:
        data = await self._request(
            "POST", "/token", params={"grant_type": "password"}, json={"email": email, "password": password}
        )
        return AuthSession.from_payload(data)

    async def send_email_otp(self, email: str, *, create_user: bool) -> None:
        await self._request("POST", "/otp", json={"email": email, "create_user": create_user})

    async def verify_email_otp(self, email: str, token: str) -> AuthSession:
        data = await self._request("POST", "/verify", json={"type": "email", "email": email, "token": token})
        return AuthSession.from_payload(data)

    async def refresh(self, refresh_token: str) -> AuthSession:
        data = await self._request(
            "POST", "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token}
        )
        return AuthSession.from_payload(data)

    async def sign_out(self, access_token: str) -> None:
        await self._request("POST", "/logout", bearer=access_token, params={"scope": "local"})

    # --- admin (service role) ------------------------------------------------------------------

    async def admin_get_user(self, uid: str) -> dict[str, Any]:
        return await self._request("GET", f"/admin/users/{uid}", admin=True)

    async def admin_create_user(
        self,
        *,
        email: str,
        password: str | None = None,
        password_hash: str | None = None,
        app_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``password_hash`` imports an existing bcrypt hash (used when migrating legacy accounts)."""
        body: dict[str, Any] = {"email": email, "email_confirm": True}
        if password:
            body["password"] = password
        elif password_hash:
            body["password_hash"] = password_hash
        if app_metadata:
            body["app_metadata"] = app_metadata
        return await self._request("POST", "/admin/users", admin=True, json=body)

    async def admin_update_user(self, uid: str, attrs: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/admin/users/{uid}", admin=True, json=attrs)

    async def admin_delete_user(self, uid: str) -> None:
        await self._request("DELETE", f"/admin/users/{uid}", admin=True)

    async def admin_find_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        email = email.strip().lower()
        page = 1
        while True:
            data = await self._request("GET", "/admin/users", admin=True, params={"page": page, "per_page": 1000})
            users = (data or {}).get("users") or []
            for u in users:
                if (u.get("email") or "").lower() == email:
                    return u
            if len(users) < 1000:
                return None
            page += 1


_client: Optional[SupabaseAuthClient] = None


def get_auth_client() -> SupabaseAuthClient:
    """FastAPI dependency / plain accessor for the process-wide client."""
    global _client
    if _client is None:
        s = get_settings()
        _client = SupabaseAuthClient(s.supabase_url, s.supabase_anon_key, s.supabase_service_role_key)
    return _client


def set_auth_client(client: Optional[SupabaseAuthClient]) -> None:
    """Swap the client (tests) or reset it (None) so it is rebuilt from settings."""
    global _client
    _client = client
