"""Object storage on Supabase Storage (private bucket, service-role access, signed download URLs).

Replaces the local ./uploads directory, which does not survive on serverless hosts.
Tests swap in ``InMemoryStorage`` via ``set_storage``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional, Protocol
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

# Prefix for storage references kept in plain string columns (e.g. attendance photo, payroll file).
STORAGE_REF_PREFIX = "storage:"


class ObjectStorage(Protocol):
    bucket: str

    async def upload(self, key: str, data: bytes, content_type: str) -> None: ...

    async def download(self, key: str) -> bytes: ...

    async def signed_url(self, key: str, expires_in: int = 900) -> str: ...


class SupabaseStorage:
    def __init__(
        self, base_url: str, service_role_key: str, bucket: str, timeout: float = 30.0, public_url: str = ""
    ):
        self.base = base_url.rstrip("/") + "/storage/v1"
        self.public_base = (public_url or base_url).rstrip("/") + "/storage/v1"
        self.key = service_role_key
        self.bucket = bucket
        self.timeout = timeout

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if not self.key:
            raise HTTPException(status_code=503, detail="File storage is not configured")
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        if extra:
            h.update(extra)
        return h

    def _obj(self, key: str) -> str:
        return f"{quote(self.bucket)}/{quote(key)}"

    async def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(method, url, **kw)
        except httpx.RequestError as e:
            logger.warning("Storage unreachable: %s", e)
            raise HTTPException(status_code=503, detail="File storage is unavailable") from e
        if r.status_code == 404 or (r.status_code == 400 and "not found" in r.text.lower()):
            raise HTTPException(status_code=404, detail="File not found")
        if r.status_code >= 400:
            logger.warning("Storage error %s: %s", r.status_code, r.text[:500])
            raise HTTPException(status_code=502, detail="File storage error")
        return r

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        await self._call(
            "POST",
            f"{self.base}/object/{self._obj(key)}",
            headers=self._headers({"Content-Type": content_type or "application/octet-stream", "x-upsert": "false"}),
            content=data,
        )

    async def download(self, key: str) -> bytes:
        r = await self._call("GET", f"{self.base}/object/authenticated/{self._obj(key)}", headers=self._headers())
        return r.content

    async def signed_url(self, key: str, expires_in: int = 900) -> str:
        r = await self._call(
            "POST",
            f"{self.base}/object/sign/{self._obj(key)}",
            headers=self._headers({"Content-Type": "application/json"}),
            json={"expiresIn": expires_in},
        )
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        if not signed:
            raise HTTPException(status_code=502, detail="File storage error")
        return f"{self.public_base}{signed}" if signed.startswith("/") else signed


class InMemoryStorage:
    """Test double."""

    def __init__(self, bucket: str = "test-bucket"):
        self.bucket = bucket
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def download(self, key: str) -> bytes:
        if key not in self.objects:
            raise HTTPException(status_code=404, detail="File not found")
        return self.objects[key][0]

    async def signed_url(self, key: str, expires_in: int = 900) -> str:
        if key not in self.objects:
            raise HTTPException(status_code=404, detail="File not found")
        return f"memory://{self.bucket}/{key}?expires_in={expires_in}"


_storage: Optional[ObjectStorage] = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        s = get_settings()
        _storage = SupabaseStorage(
            s.supabase_url, s.supabase_service_role_key, s.supabase_storage_bucket, public_url=s.supabase_public_url
        )
    return _storage


def set_storage(storage: Optional[ObjectStorage]) -> None:
    global _storage
    _storage = storage


def new_object_key(folder: str, filename: str | None, default_ext: str) -> str:
    ext = ""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[1].lower()[:10]
    if not ext or not ext[1:].isalnum():
        ext = default_ext
    return f"{folder}/{uuid.uuid4().hex}{ext}"


def storage_ref(key: str) -> str:
    return f"{STORAGE_REF_PREFIX}{key}"
