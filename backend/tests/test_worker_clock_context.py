"""Worker clock context API: completed assignments remain addressable after clock-out."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

# App imports build a module-level async engine; use SQLite so tests run without Postgres/asyncpg.
os.environ["SECRET_KEY"] = "test-secret-key-for-jwt-tests-only-32b"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.routers import worker_app as worker_app_router

_worker_clock_test_app = FastAPI()
_worker_clock_test_app.include_router(worker_app_router.router)
from app.models import (
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    PayModel,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)

get_settings.cache_clear()


@pytest.fixture
def client_with_completed_assignment():
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
            u_client = User(phone="+19990000001", role=UserRole.client, locale="en", client_org_id=org.id)
            u_worker = User(phone="+19990000002", role=UserRole.worker, locale="en")
            s.add_all([u_client, u_worker])
            await s.flush()
            wp = WorkerProfile(
                user_id=u_worker.id,
                full_name="Jane Worker",
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
            shift = Shift(job_order_id=job.id, starts_at=now, ends_at=now + timedelta(hours=8), workers_required=1)
            s.add(shift)
            await s.flush()
            sa = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.completed)
            s.add(sa)
            await s.flush()
            rec = AttendanceRecord(
                shift_assignment_id=sa.id,
                clock_in_time=now,
                clock_out_time=now + timedelta(hours=7),
                total_hours=7.0,
                status=AttendanceStatus.present,
            )
            s.add(rec)
            await s.commit()
            return engine, Session, u_worker.id, sa.id

    engine, Session, worker_user_id, assignment_id = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _worker_clock_test_app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(worker_user_id), {})
    try:
        with TestClient(_worker_clock_test_app) as client:
            yield client, token, assignment_id
    finally:
        _worker_clock_test_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_clock_context_completed_assignment_returns_200(client_with_completed_assignment):
    client, token, assignment_id = client_with_completed_assignment
    r = client.get(
        f"/worker/clock/context?assignment_id={assignment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["assignment"] is not None
    assert data["assignment"]["assignment_id"] == assignment_id
    assert data["assignment"]["already_clocked_in"] is True
    assert data["assignment"]["already_clocked_out"] is True


@pytest.fixture
def client_with_confirmed_assignment_past_grace():
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
            u_client = User(phone="+19990000101", role=UserRole.client, locale="en", client_org_id=org.id)
            u_worker = User(phone="+19990000102", role=UserRole.worker, locale="en")
            s.add_all([u_client, u_worker])
            await s.flush()
            wp = WorkerProfile(
                user_id=u_worker.id,
                full_name="Late Worker",
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
                title="Morning shift",
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
                starts_at=now - timedelta(minutes=20),
                ends_at=now + timedelta(hours=7, minutes=40),
                workers_required=1,
            )
            s.add(shift)
            await s.flush()
            sa = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.confirmed)
            s.add(sa)
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

    _worker_clock_test_app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(worker_user_id), {})
    try:
        with TestClient(_worker_clock_test_app) as client:
            yield client, token
    finally:
        _worker_clock_test_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_clock_context_blocks_clock_in_after_grace(client_with_confirmed_assignment_past_grace):
    client, token = client_with_confirmed_assignment_past_grace
    r = client.get(
        "/worker/clock/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["assignment"] is not None
    assert data["assignment"]["already_clocked_in"] is False
    assert data["assignment"]["can_clock_in_now"] is False
    assert data["assignment"]["is_late_window"] is True
