from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import (
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    PayModel,
    RecommendationRefreshQueue,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.services import timesheet_flow
from app.services.recommendation_refresh import enqueue_worker_recommendation_refresh


@pytest_asyncio.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_shift_enqueues_recommendation_refresh(sqlite_session: AsyncSession):
    now = datetime.now(timezone.utc)

    org = ClientOrganization(name="ACME")
    sqlite_session.add(org)
    await sqlite_session.flush()
    client_user = User(phone="+17770000001", role=UserRole.client, locale="en", client_org_id=org.id)
    worker_user = User(phone="+17770000002", role=UserRole.worker, locale="en")
    sqlite_session.add_all([client_user, worker_user])
    await sqlite_session.flush()

    worker = WorkerProfile(
        user_id=worker_user.id,
        full_name="Worker Trigger",
        languages=[],
        job_type_preferences=[],
        skills=[],
        certifications=[],
        availability={},
        tier=WorkerTier.standard,
        status=WorkerStatus.active,
    )
    sqlite_session.add(worker)
    await sqlite_session.flush()

    job = JobOrder(
        client_org_id=org.id,
        created_by_user_id=client_user.id,
        title="Trigger Job",
        job_type="Warehouse Worker",
        location_address="SG",
        location_lat=1.0,
        location_lng=103.0,
        workers_required_total=1,
        pay_model=PayModel.hourly,
        hourly_rate=18.0,
        status=JobOrderStatus.open,
    )
    sqlite_session.add(job)
    await sqlite_session.flush()
    shift = Shift(
        job_order_id=job.id,
        starts_at=now - timedelta(hours=8),
        ends_at=now - timedelta(hours=1),
        workers_required=1,
    )
    sqlite_session.add(shift)
    await sqlite_session.flush()
    assignment = ShiftAssignment(
        shift_id=shift.id,
        worker_id=worker.id,
        status=AssignmentStatus.confirmed,
    )
    sqlite_session.add(assignment)
    await sqlite_session.flush()
    attendance = AttendanceRecord(
        shift_assignment_id=assignment.id,
        clock_in_time=now - timedelta(hours=8),
        clock_out_time=now - timedelta(hours=1),
        total_hours=7.0,
        status=AttendanceStatus.present,
    )
    sqlite_session.add(attendance)
    await sqlite_session.flush()

    await timesheet_flow.mark_assignment_completed_if_clocked(sqlite_session, assignment, attendance)
    assert assignment.status == AssignmentStatus.completed
    queued = (await sqlite_session.execute(select(RecommendationRefreshQueue.worker_profile_id))).scalars().all()
    assert queued == [worker.id]

    # Enqueueing again is idempotent (one row per worker).
    await enqueue_worker_recommendation_refresh(sqlite_session, worker.id)
    queued = (await sqlite_session.execute(select(RecommendationRefreshQueue.worker_profile_id))).scalars().all()
    assert queued == [worker.id]
