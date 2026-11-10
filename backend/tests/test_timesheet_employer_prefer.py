"""Employer endorsement: prefer_worker No must not remove existing PreferredWorker."""

import asyncio
import os

# Must be set before importing app.db.session (engine creation).
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    Shift,
    ShiftAssignment,
    TimesheetEntry,
    TimesheetStatus,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import timesheets as timesheets_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(timesheets_router.router)


@pytest.fixture
def endorse_client_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            org = ClientOrganization(name="Org")
            s.add(org)
            await s.flush()
            u_client = User(phone="+19992000001", role=UserRole.client, locale="en", client_org_id=org.id)
            u_worker = User(phone="+19992000002", role=UserRole.worker, locale="en")
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
                tier=WorkerTier.standard,
                status=WorkerStatus.active,
            )
            s.add(wp)
            await s.flush()
            job = JobOrder(
                client_org_id=org.id,
                created_by_user_id=u_client.id,
                title="Shift",
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
            entry = TimesheetEntry(
                job_order_id=job.id,
                shift_id=shift.id,
                worker_id=wp.id,
                shift_assignment_id=sa.id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=160.0,
                status=TimesheetStatus.pending,
            )
            s.add(entry)
            s.add(PreferredWorker(client_org_id=org.id, worker_id=wp.id))
            await s.commit()
            return engine, Session, u_client.id, entry.id, org.id, wp.id

    engine, Session, client_user_id, entry_id, org_id, worker_profile_id = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(client_user_id), {})
    try:
        with TestClient(_app) as client:
            yield client, Session, token, entry_id, org_id, worker_profile_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_employer_endorse_prefer_no_keeps_preferred_worker(endorse_client_setup):
    client, Session, token, entry_id, org_id, worker_profile_id = endorse_client_setup
    r = client.post(
        f"/timesheets/{entry_id}/employer-endorse",
        headers={"Authorization": f"Bearer {token}"},
        json={"stars": 5, "prefer_worker": False},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    async def check_pref():
        async with Session() as s:
            q = await s.execute(
                select(PreferredWorker).where(
                    PreferredWorker.client_org_id == org_id,
                    PreferredWorker.worker_id == worker_profile_id,
                )
            )
            return q.scalar_one_or_none()

    pw = asyncio.run(check_pref())
    assert pw is not None
