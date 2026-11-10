"""Client self-registration via email OTP (dev mode)."""

import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("OTP_DEV_MODE", "true")

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import User, UserRole
from app.routers import auth as auth_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(auth_router.router)


@pytest.fixture
def client():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine

    engine = asyncio.run(init())
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    with TestClient(_app) as tc:
        yield tc
    _app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def test_client_email_otp_creates_account(client: TestClient):
    r1 = client.post("/auth/client/email-otp/request", json={"email": "new.client@example.com", "locale": "en"})
    assert r1.status_code == 200
    # Codes are emailed by Supabase Auth; only the local Mailpit hook echoes them back.
    assert r1.json()["delivery"] == "email" and r1.json()["dev_code"] is None

    r2 = client.post(
        "/auth/client/email-otp/verify",
        json={"email": "new.client@example.com", "code": "123456", "locale": "en"},
    )
    assert r2.status_code == 200
    payload = r2.json()
    assert payload["role"] == "client"
    assert payload.get("access_token")


def test_client_email_otp_conflict_when_email_used_by_worker():
    async def setup_engine():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as s:
            s.add(User(email="shared@example.com", phone=None, role=UserRole.worker, locale="en"))
            await s.commit()

        async def override_get_db():
            async with Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app = FastAPI()
        app.include_router(auth_router.router)
        app.dependency_overrides[get_db] = override_get_db
        return app, engine

    app, engine = asyncio.run(setup_engine())
    try:
        with TestClient(app) as client:
            client.post("/auth/client/email-otp/request", json={"email": "shared@example.com"})
            r = client.post("/auth/client/email-otp/verify", json={"email": "shared@example.com", "code": "123456"})
            assert r.status_code == 409
    finally:
        asyncio.run(engine.dispose())
