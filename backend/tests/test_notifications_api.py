"""HTTP tests for /notifications list and clear (user isolation)."""

import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import Notification, User, UserRole
from app.routers import notifications as notifications_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(notifications_router.router)


@pytest.fixture
def notifications_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as s:
            a = User(phone="+17770002001", role=UserRole.worker, locale="en")
            b = User(phone="+17770002002", role=UserRole.worker, locale="en")
            s.add_all([a, b])
            await s.flush()
            s.add(
                Notification(
                    user_id=a.id,
                    title="For A",
                    body="alpha",
                    payload={},
                )
            )
            s.add(
                Notification(
                    user_id=b.id,
                    title="For B",
                    body="beta",
                    payload={},
                )
            )
            await s.commit()
            return engine, Session, a.id, b.id

    engine, Session, id_a, id_b = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    tokens = {"a": create_access_token(str(id_a), {}), "b": create_access_token(str(id_b), {})}
    try:
        with TestClient(_app) as client:
            yield client, Session, tokens
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_notifications_list_isolated_per_user(notifications_setup):
    client, _Session, tokens = notifications_setup
    ra = client.get("/notifications", headers={"Authorization": f"Bearer {tokens['a']}"})
    assert ra.status_code == 200
    ja = ra.json()
    assert len(ja) == 1
    assert ja[0]["title"] == "For A"

    rb = client.get("/notifications", headers={"Authorization": f"Bearer {tokens['b']}"})
    assert rb.status_code == 200
    jb = rb.json()
    assert len(jb) == 1
    assert jb[0]["title"] == "For B"


def test_notifications_unauthenticated(notifications_setup):
    client, _Session, _tokens = notifications_setup
    r = client.get("/notifications")
    assert r.status_code == 401


def test_notifications_delete_clears_only_current_user(notifications_setup):
    client, Session, tokens = notifications_setup
    r = client.delete("/notifications", headers={"Authorization": f"Bearer {tokens['a']}"})
    assert r.status_code == 204

    async def count_all():
        async with Session() as s:
            r2 = await s.execute(select(func.count()).select_from(Notification))
            return r2.scalar_one()

    remaining = asyncio.run(count_all())
    assert remaining == 1

    rb = client.get("/notifications", headers={"Authorization": f"Bearer {tokens['b']}"})
    assert rb.status_code == 200
    assert len(rb.json()) == 1
