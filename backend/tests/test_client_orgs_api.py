"""HTTP tests for /client-organizations list (TA vs admin vs client)."""

import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import ClientOrganization, TaClientContract, TaClientContractStatus, User, UserRole
from app.routers import client_orgs as client_orgs_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(client_orgs_router.router)


@pytest.fixture
def orgs_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as s:
            o1 = ClientOrganization(name="Alpha Org")
            o2 = ClientOrganization(name="Beta Org")
            s.add_all([o1, o2])
            await s.flush()
            ta = User(phone="+19990004001", role=UserRole.ta, locale="en")
            admin = User(phone="+19990004002", role=UserRole.admin, locale="en")
            client_u = User(
                phone="+19990004003",
                role=UserRole.client,
                locale="en",
                client_org_id=o1.id,
            )
            s.add_all([ta, admin, client_u])
            await s.flush()
            s.add(
                TaClientContract(
                    client_org_id=o1.id,
                    ta_user_id=ta.id,
                    status=TaClientContractStatus.pending_ta,
                    requested_by_user_id=client_u.id,
                )
            )
            await s.commit()
            return engine, Session, ta.id, admin.id, client_u.id

    engine, Session, ta_id, admin_id, client_id = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    tokens = {
        "ta": create_access_token(str(ta_id), {}),
        "admin": create_access_token(str(admin_id), {}),
        "client": create_access_token(str(client_id), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, tokens
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_client_orgs_forbidden_for_client_user(orgs_setup):
    client, tokens = orgs_setup
    r = client.get("/client-organizations", headers={"Authorization": f"Bearer {tokens['client']}"})
    assert r.status_code == 403


def test_client_orgs_admin_sees_all(orgs_setup):
    client, tokens = orgs_setup
    r = client.get("/client-organizations", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200
    names = {x["name"] for x in r.json()}
    assert names == {"Alpha Org", "Beta Org"}


def test_client_orgs_ta_sees_only_visible_contracts(orgs_setup):
    client, tokens = orgs_setup
    r = client.get("/client-organizations", headers={"Authorization": f"Bearer {tokens['ta']}"})
    assert r.status_code == 200
    names = {x["name"] for x in r.json()}
    assert names == {"Alpha Org"}
