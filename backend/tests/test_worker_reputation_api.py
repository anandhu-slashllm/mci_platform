"""GET /workers/me/reputation returns ratings and preferred client orgs."""

import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    PayModel,
    PreferredWorker,
    Rating,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import workers as workers_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(workers_router.router)


@pytest.fixture
def reputation_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            org = ClientOrganization(name="Acme Corp")
            s.add(org)
            await s.flush()
            u_client = User(phone="+19993000001", role=UserRole.client, locale="en", client_org_id=org.id)
            u_worker = User(phone="+19993000002", role=UserRole.worker, locale="en")
            s.add_all([u_client, u_worker])
            await s.flush()
            wp = WorkerProfile(
                user_id=u_worker.id,
                full_name="Worker",
                languages=[],
                job_type_preferences=[],
                skills=[],
                certifications=[],
                availability={},
                tier=WorkerTier.probation,
                status=WorkerStatus.active,
                rating_avg=4.5,
                points=-5,
            )
            s.add(wp)
            await s.flush()
            job = JobOrder(
                client_org_id=org.id,
                created_by_user_id=u_client.id,
                title="Night shift",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.open,
            )
            s.add(job)
            await s.flush()
            shift = Shift(
                job_order_id=job.id,
                starts_at=now,
                ends_at=now + timedelta(hours=8),
                workers_required=1,
            )
            s.add(shift)
            await s.flush()
            sa = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.confirmed)
            s.add(sa)
            await s.flush()
            s.add(
                Rating(
                    shift_assignment_id=sa.id,
                    worker_id=wp.id,
                    rated_by_user_id=u_client.id,
                    stars=5,
                    comment="Great",
                )
            )
            s.add(PreferredWorker(client_org_id=org.id, worker_id=wp.id))
            await s.commit()
            return engine, Session, u_worker.id

    engine, Session, worker_user_id = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(worker_user_id), {})
    try:
        with TestClient(_app) as client:
            yield client, token
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_worker_reputation_endpoint(reputation_setup):
    client, token = reputation_setup
    r = client.get("/workers/me/reputation", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["rating_avg"] == 4.5
    assert data["points"] == -5
    assert data["tier"] == "probation"
    assert len(data["ratings"]) == 1
    assert data["ratings"][0]["stars"] == 5
    assert data["ratings"][0]["comment"] == "Great"
    assert data["ratings"][0]["client_org_name"] == "Acme Corp"
    assert data["ratings"][0]["job_title"] == "Night shift"
    assert len(data["preferred_by_clients"]) == 1
    assert data["preferred_by_clients"][0]["name"] == "Acme Corp"
