"""HTTP tests for client preferred workers and live attendance org scoping."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

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
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    PayModel,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerTier,
)
from app.routers import client_portal as client_portal_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(client_portal_router.router)


@pytest.fixture
def portal_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            org_a = ClientOrganization(name="Client A")
            org_b = ClientOrganization(name="Client B")
            s.add_all([org_a, org_b])
            await s.flush()
            client_a = User(
                phone="+19990005001",
                role=UserRole.client,
                locale="en",
                client_org_id=org_a.id,
            )
            client_b = User(
                phone="+19990005002",
                role=UserRole.client,
                locale="en",
                client_org_id=org_b.id,
            )
            wu = User(phone="+19990005003", role=UserRole.worker, locale="en")
            s.add_all([client_a, client_b, wu])
            await s.flush()
            wp = WorkerProfile(
                user_id=wu.id,
                full_name="Live Worker",
                languages=["en"],
                job_type_preferences=["Warehouse Worker"],
                skills=[],
                certifications=[],
                availability={},
                tier=WorkerTier.standard,
            )
            s.add(wp)
            await s.flush()
            job_b = JobOrder(
                client_org_id=org_b.id,
                created_by_user_id=client_b.id,
                title="Other job",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=18.0,
                status=JobOrderStatus.open,
            )
            job_a = JobOrder(
                client_org_id=org_a.id,
                created_by_user_id=client_a.id,
                title="Our job",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=18.0,
                status=JobOrderStatus.open,
            )
            s.add_all([job_a, job_b])
            await s.flush()
            shift_a = Shift(
                job_order_id=job_a.id,
                starts_at=now - timedelta(minutes=30),
                ends_at=now + timedelta(hours=6),
                workers_required=1,
            )
            s.add(shift_a)
            await s.flush()
            s.add(
                ShiftAssignment(
                    shift_id=shift_a.id,
                    worker_id=wp.id,
                    status=AssignmentStatus.confirmed,
                )
            )
            await s.commit()
            return engine, Session, client_a.id, job_a.id, job_b.id, wp.id

    engine, Session, client_a_id, job_a_id, job_b_id, wp_id = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    token_a = create_access_token(str(client_a_id), {})
    try:
        with TestClient(_app) as client:
            yield client, token_a, job_a_id, job_b_id, wp_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_preferred_worker_idempotent_and_delete(portal_setup):
    client, token_a, _job_a_id, _job_b_id, wp_id = portal_setup
    h = {"Authorization": f"Bearer {token_a}"}
    r1 = client.post(f"/client/preferred-workers/{wp_id}", headers=h)
    assert r1.status_code == 200
    assert r1.json()["ok"] is True
    r2 = client.post(f"/client/preferred-workers/{wp_id}", headers=h)
    assert r2.status_code == 200
    r3 = client.delete(f"/client/preferred-workers/{wp_id}", headers=h)
    assert r3.status_code == 200


def test_live_attendance_404_for_other_org_job(portal_setup):
    client, token_a, _job_a_id, job_b_id, _wp_id = portal_setup
    r = client.get(
        f"/client/job-orders/{job_b_id}/live-attendance",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404


def test_live_attendance_ok_for_own_org(portal_setup):
    client, token_a, job_a_id, _job_b_id, _wp_id = portal_setup
    r = client.get(
        f"/client/job-orders/{job_a_id}/live-attendance",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "workers" in body
    assert len(body["workers"]) >= 1
