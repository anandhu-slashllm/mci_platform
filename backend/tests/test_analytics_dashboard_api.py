"""Analytics dashboard API scope and payload tests."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    Notification,
    PayModel,
    Rating,
    Shift,
    ShiftAssignment,
    TaClientContract,
    TaClientContractStatus,
    TimesheetEntry,
    TimesheetStatus,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
)
from app.routers import analytics as analytics_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(analytics_router.router)


async def _seed():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    async with Session() as session:
        org1 = ClientOrganization(name="Acme Logistics")
        org2 = ClientOrganization(name="Other Client")
        session.add_all([org1, org2])
        await session.flush()

        client_user = User(phone="+16660000001", role=UserRole.client, locale="en", client_org_id=org1.id)
        ta_user = User(phone="+16660000002", role=UserRole.ta, locale="en")
        other_ta = User(phone="+16660000003", role=UserRole.ta, locale="en")
        worker_user1 = User(phone="+16660000004", role=UserRole.worker, locale="en")
        worker_user2 = User(phone="+16660000005", role=UserRole.worker, locale="en")
        worker_user3 = User(phone="+16660000006", role=UserRole.worker, locale="en")
        session.add_all([client_user, ta_user, other_ta, worker_user1, worker_user2, worker_user3])
        await session.flush()

        session.add(
            TaClientContract(
                client_org_id=org1.id,
                ta_user_id=ta_user.id,
                status=TaClientContractStatus.active,
                requested_by_user_id=client_user.id,
            )
        )
        session.add(
            TaClientContract(
                client_org_id=org2.id,
                ta_user_id=other_ta.id,
                status=TaClientContractStatus.active,
                requested_by_user_id=other_ta.id,
            )
        )

        worker1 = WorkerProfile(
            user_id=worker_user1.id,
            full_name="Worker One",
            rating_avg=4.6,
            completion_rate=0.9,
            status=WorkerStatus.active,
        )
        worker2 = WorkerProfile(
            user_id=worker_user2.id,
            full_name="Worker Two",
            rating_avg=4.2,
            completion_rate=0.7,
            status=WorkerStatus.active,
        )
        worker3 = WorkerProfile(
            user_id=worker_user3.id,
            full_name="Other Scope Worker",
            rating_avg=4.9,
            completion_rate=1.0,
            status=WorkerStatus.active,
        )
        session.add_all([worker1, worker2, worker3])
        await session.flush()

        job1 = JobOrder(
            client_org_id=org1.id,
            created_by_user_id=client_user.id,
            title="Acme Warehouse",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.0,
            location_lng=103.0,
            workers_required_total=3,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
            created_at=now - timedelta(days=70),
        )
        job2 = JobOrder(
            client_org_id=org2.id,
            created_by_user_id=other_ta.id,
            title="Other Scope",
            job_type="Event Staff",
            location_address="SG",
            location_lat=1.0,
            location_lng=103.0,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=18.0,
            status=JobOrderStatus.open,
            created_at=now - timedelta(days=20),
        )
        session.add_all([job1, job2])
        await session.flush()

        shift_hist = Shift(
            job_order_id=job1.id,
            starts_at=now - timedelta(days=21),
            ends_at=now - timedelta(days=21) + timedelta(hours=8),
            workers_required=2,
        )
        shift_recent = Shift(
            job_order_id=job1.id,
            starts_at=now - timedelta(days=7),
            ends_at=now - timedelta(days=7) + timedelta(hours=8),
            workers_required=1,
        )
        shift_future = Shift(
            job_order_id=job1.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=8),
            workers_required=2,
        )
        shift_other = Shift(
            job_order_id=job2.id,
            starts_at=now - timedelta(days=5),
            ends_at=now - timedelta(days=5) + timedelta(hours=6),
            workers_required=1,
        )
        session.add_all([shift_hist, shift_recent, shift_future, shift_other])
        await session.flush()

        assignment_completed = ShiftAssignment(
            shift_id=shift_hist.id,
            worker_id=worker1.id,
            status=AssignmentStatus.completed,
        )
        assignment_no_show = ShiftAssignment(
            shift_id=shift_hist.id,
            worker_id=worker2.id,
            status=AssignmentStatus.no_show,
        )
        assignment_future = ShiftAssignment(
            shift_id=shift_future.id,
            worker_id=worker1.id,
            status=AssignmentStatus.confirmed,
        )
        assignment_cancelled = ShiftAssignment(
            shift_id=shift_recent.id,
            worker_id=worker2.id,
            status=AssignmentStatus.cancelled,
        )
        assignment_other = ShiftAssignment(
            shift_id=shift_other.id,
            worker_id=worker3.id,
            status=AssignmentStatus.completed,
        )
        session.add_all(
            [
                assignment_completed,
                assignment_no_show,
                assignment_future,
                assignment_cancelled,
                assignment_other,
            ]
        )
        await session.flush()

        session.add(
            Notification(
                user_id=ta_user.id,
                title="Late cancellation alert",
                body="Worker cancelled late",
                payload={
                    "type": "late_cancellation",
                    "worker_profile_id": worker2.id,
                    "assignment_id": assignment_cancelled.id,
                    "shift_id": shift_recent.id,
                },
                created_at=now - timedelta(days=6),
            )
        )

        session.add(
            TimesheetEntry(
                job_order_id=job1.id,
                shift_id=shift_hist.id,
                worker_id=worker1.id,
                shift_assignment_id=assignment_completed.id,
                scheduled_hours=8.0,
                actual_hours=7.5,
                gross_pay=150.0,
                status=TimesheetStatus.payment_released,
            )
        )
        session.add(
            Rating(
                shift_assignment_id=assignment_completed.id,
                worker_id=worker1.id,
                rated_by_user_id=client_user.id,
                stars=5,
            )
        )

        await session.commit()
        return engine, Session, client_user.id, ta_user.id, org1.id, org2.id


def test_client_dashboard_is_scoped_to_own_org():
    engine, Session, client_user_id, _ta_user_id, org1_id, _org2_id = asyncio.run(_seed())

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
            res = client.get(
                "/analytics/portal-dashboard?period_days=90&bucket=week",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["scope"]["client_org_ids"] == [org1_id]
            assert body["scope"]["available_clients"] == [{"id": org1_id, "name": "Acme Logistics"}]
            assert body["summary"]["active_workers"] == 2
            assert body["summary"]["active_job_orders"] == 1
            assert body["summary"]["open_positions"] == 1
            assert body["summary"]["upcoming_shifts"] == 1
            assert len(body["demand_supply"]["points"]) > 0
            assert body["worker_performance"]["summary"]["average_rating"] >= 4.5
            assert body["incident_trends"]["points"]
            assert body["payroll_summary"]["totals"]["hours_worked"] == 7.5
            assert body["payroll_summary"]["by_client"] == [
                {
                    "client_org_id": org1_id,
                    "client_name": "Acme Logistics",
                    "hours_worked": 7.5,
                    "gross_pay": 150.0,
                }
            ]
            assert body["forecast"]["points"]
            assert body["retention_cohorts"]["cohorts"]
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_ta_dashboard_is_limited_to_contracted_clients():
    engine, Session, _client_user_id, ta_user_id, org1_id, org2_id = asyncio.run(_seed())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(ta_user_id), {})
    try:
        with TestClient(_app) as client:
            res = client.get("/analytics/portal-dashboard?bucket=week", headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["scope"]["client_org_ids"] == [org1_id]
            assert body["scope"]["available_clients"] == [{"id": org1_id, "name": "Acme Logistics"}]
            assert all(row["client_org_id"] == org1_id for row in body["fill_rate_breakdowns"]["by_client"])

            filtered = client.get(
                f"/analytics/portal-dashboard?client_org_id={org1_id}&bucket=week",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["scope"]["selected_client_org_id"] == org1_id

            forbidden = client.get(
                f"/analytics/portal-dashboard?client_org_id={org2_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert forbidden.status_code == 403, forbidden.text
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
