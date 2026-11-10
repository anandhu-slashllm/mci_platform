import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("OTP_DEV_MODE", "true")

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token, auth_uid_for_user
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import User, UserRole
from app.routers import ta_portal as ta_portal_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(ta_portal_router.router)


@pytest.fixture
def ta_client_ctx():
    async def setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as session:
            ta = User(phone="+ta_prof_1", role=UserRole.ta, email="ta_before@example.com")
            other = User(phone="+ta_prof_2", role=UserRole.worker, email="taken@example.com")
            session.add_all([ta, other])
            await session.commit()
            await session.refresh(ta)
            await session.refresh(other)
            ta_id = ta.id
            other_id = other.id
        return engine, Session, ta_id, other_id

    engine, Session, ta_id, _other_id = asyncio.run(setup())

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
        yield tc, ta_id
    _app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def test_ta_get_and_update_email(ta_client_ctx, fake_auth):
    tc, ta_id = ta_client_ctx
    token = create_access_token(str(ta_id), {"role": "ta"})
    auth_uid = fake_auth.add_user("ta_before@example.com", uid=auth_uid_for_user(ta_id))
    h = {"Authorization": f"Bearer {token}"}

    r = tc.get("/ta/me/profile", headers=h)
    assert r.status_code == 200
    assert r.json()["email"] == "ta_before@example.com"
    assert r.json().get("display_name") in (None, "")

    r2 = tc.put(
        "/ta/me/profile",
        headers=h,
        json={"display_name": "Agent One", "email": "ta_after@example.com"},
    )
    assert r2.status_code == 200
    assert r2.json()["email"] == "ta_after@example.com"
    assert r2.json()["display_name"] == "Agent One"
    # The Supabase login email follows the profile email.
    assert fake_auth.users[auth_uid]["email"] == "ta_after@example.com"

    r3 = tc.get("/ta/me/profile", headers=h)
    assert r3.json()["email"] == "ta_after@example.com"
    assert r3.json()["display_name"] == "Agent One"

    r4 = tc.put("/ta/me/profile", headers=h, json={"display_name": "", "email": "ta_after@example.com"})
    assert r4.status_code == 200
    assert r4.json().get("display_name") in (None, "")


def test_ta_email_conflict(ta_client_ctx):
    tc, ta_id = ta_client_ctx
    token = create_access_token(str(ta_id), {"role": "ta"})
    h = {"Authorization": f"Bearer {token}"}

    r = tc.put("/ta/me/profile", headers=h, json={"email": "taken@example.com"})
    assert r.status_code == 409
    assert "already" in r.json()["detail"].lower()
