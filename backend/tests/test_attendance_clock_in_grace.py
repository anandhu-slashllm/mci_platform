import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.attendance_qr_tokens import create_attendance_qr_token
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
    WorkerStatus,
    WorkerTier,
)
from app.routers import attendance as attendance_router

os.environ["SECRET_KEY"] = "test-secret-key-for-jwt-tests-only-32b"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
get_settings.cache_clear()

_app = FastAPI()
_app.include_router(attendance_router.router)


@pytest.fixture
def client_with_shift():
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
            u_client = User(phone="+19991000001", role=UserRole.client, locale="en", client_org_id=org.id)
            u_worker = User(phone="+19991000002", role=UserRole.worker, locale="en")
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
                title="Grace test",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                geofence_radius_m=5000,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.open,
            )
            s.add(job)
            await s.flush()
            await s.commit()
            return engine, Session, u_worker.id, wp.id, job.id

    engine, Session, worker_user_id, worker_profile_id, job_id = asyncio.run(init())

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
            yield client, Session, token, worker_profile_id, job_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _insert_shift_and_assignment(
    Session: async_sessionmaker[AsyncSession],
    worker_profile_id: int,
    job_id: int,
    starts_at: datetime,
):
    async with Session() as s:
        shift = Shift(
            job_order_id=job_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=8),
            workers_required=1,
        )
        s.add(shift)
        await s.flush()
        sa = ShiftAssignment(
            shift_id=shift.id,
            worker_id=worker_profile_id,
            status=AssignmentStatus.confirmed,
        )
        s.add(sa)
        await s.commit()
        return shift.id


def test_clock_in_within_grace_marks_late(client_with_shift):
    client, Session, token, worker_profile_id, job_id = client_with_shift
    now = datetime.now(timezone.utc)
    shift_id = asyncio.run(
        _insert_shift_and_assignment(
            Session,
            worker_profile_id,
            job_id,
            starts_at=now - timedelta(minutes=5),
        )
    )
    qr = create_attendance_qr_token(job_id=job_id, shift_id=shift_id, action="in", shift_ends_at=now + timedelta(hours=1))
    r = client.post(
        "/attendance/qr-clock",
        headers={"Authorization": f"Bearer {token}"},
        json={"token": qr, "lat": 1.0, "lng": 103.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "late"


def test_clock_in_after_grace_is_blocked(client_with_shift):
    client, Session, token, worker_profile_id, job_id = client_with_shift
    now = datetime.now(timezone.utc)
    shift_id = asyncio.run(
        _insert_shift_and_assignment(
            Session,
            worker_profile_id,
            job_id,
            starts_at=now - timedelta(minutes=11),
        )
    )
    qr = create_attendance_qr_token(job_id=job_id, shift_id=shift_id, action="in", shift_ends_at=now + timedelta(hours=1))
    r = client.post(
        "/attendance/qr-clock",
        headers={"Authorization": f"Bearer {token}"},
        json={"token": qr, "lat": 1.0, "lng": 103.0},
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "clock_in_grace_expired"
