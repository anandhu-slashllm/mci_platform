"""Worker GET /timesheets: earnings rows only after client or TA endorsement (per payroll model)."""

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
    Shift,
    ShiftAssignment,
    TaClientContract,
    TaClientContractStatus,
    TimesheetEntry,
    TimesheetStatus,
    User,
    UserRole,
    WorkerProfile,
)
from app.routers import timesheets as timesheets_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(timesheets_router.router)


@pytest.fixture
def worker_timesheet_filter_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            org_direct = ClientOrganization(name="Direct Payroll Org")
            org_ta = ClientOrganization(name="TA Payroll Org")
            s.add_all([org_direct, org_ta])
            await s.flush()
            u_client = User(
                phone="+19994000001",
                role=UserRole.client,
                locale="en",
                client_org_id=org_direct.id,
            )
            u_client_ta = User(
                phone="+19994000002",
                role=UserRole.client,
                locale="en",
                client_org_id=org_ta.id,
            )
            u_worker = User(phone="+19994000003", role=UserRole.worker, locale="en")
            u_ta = User(phone="+19994000004", role=UserRole.ta, locale="en")
            s.add_all([u_client, u_client_ta, u_worker, u_ta])
            await s.flush()
            wp = WorkerProfile(user_id=u_worker.id, full_name="Filter Worker")
            s.add(wp)
            await s.flush()

            job_direct = JobOrder(
                client_org_id=org_direct.id,
                created_by_user_id=u_client.id,
                title="Direct job",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=4,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.completed,
            )
            job_ta = JobOrder(
                client_org_id=org_ta.id,
                created_by_user_id=u_client_ta.id,
                title="TA job",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=4,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.completed,
            )
            s.add_all([job_direct, job_ta])
            await s.flush()

            shifts = []
            for i, job in enumerate((job_direct, job_ta, job_direct, job_ta)):
                sh = Shift(
                    job_order_id=job.id,
                    starts_at=now + timedelta(hours=i),
                    ends_at=now + timedelta(hours=i + 8),
                    workers_required=1,
                )
                shifts.append(sh)
            s.add_all(shifts)
            await s.flush()

            assignments = []
            for sh in shifts:
                sa = ShiftAssignment(shift_id=sh.id, worker_id=wp.id, status=AssignmentStatus.completed)
                assignments.append(sa)
            s.add_all(assignments)
            await s.flush()

            ts_direct_pending = TimesheetEntry(
                job_order_id=job_direct.id,
                shift_id=shifts[0].id,
                worker_id=wp.id,
                shift_assignment_id=assignments[0].id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=100.0,
                status=TimesheetStatus.pending,
            )
            ts_ta_endorsed_only = TimesheetEntry(
                job_order_id=job_ta.id,
                shift_id=shifts[1].id,
                worker_id=wp.id,
                shift_assignment_id=assignments[1].id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=300.0,
                status=TimesheetStatus.employer_endorsed,
            )
            ts_direct_endorsed = TimesheetEntry(
                job_order_id=job_direct.id,
                shift_id=shifts[2].id,
                worker_id=wp.id,
                shift_assignment_id=assignments[2].id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=200.0,
                status=TimesheetStatus.employer_endorsed,
            )
            ts_ta_verified = TimesheetEntry(
                job_order_id=job_ta.id,
                shift_id=shifts[3].id,
                worker_id=wp.id,
                shift_assignment_id=assignments[3].id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=400.0,
                status=TimesheetStatus.ta_verified,
            )
            s.add_all([ts_direct_pending, ts_ta_endorsed_only, ts_direct_endorsed, ts_ta_verified])
            s.add(
                TaClientContract(
                    client_org_id=org_ta.id,
                    ta_user_id=u_ta.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=u_client_ta.id,
                )
            )
            await s.commit()
            return (
                engine,
                Session,
                u_worker.id,
                ts_direct_pending.id,
                ts_direct_endorsed.id,
                ts_ta_endorsed_only.id,
                ts_ta_verified.id,
            )

    (
        engine,
        Session,
        worker_user_id,
        id_pending,
        id_direct_endorsed,
        id_ta_endorsed_only,
        id_ta_verified,
    ) = asyncio.run(init())

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
    ids = {
        "pending": id_pending,
        "direct_endorsed": id_direct_endorsed,
        "ta_endorsed_only": id_ta_endorsed_only,
        "ta_verified": id_ta_verified,
    }
    try:
        with TestClient(_app) as client:
            yield client, token, ids
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_worker_timesheet_list_shows_only_endorsed_or_ta_verified(worker_timesheet_filter_setup):
    client, token, ids = worker_timesheet_filter_setup
    r = client.get("/timesheets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    returned = {row["id"] for row in r.json()}
    assert ids["pending"] not in returned
    assert ids["ta_endorsed_only"] not in returned
    assert ids["direct_endorsed"] in returned
    assert ids["ta_verified"] in returned
