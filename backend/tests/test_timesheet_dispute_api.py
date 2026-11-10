"""Timesheet dispute API coverage."""

import asyncio
import os

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
    Notification,
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
def dispute_setup():
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
            u_client = User(phone="+19993000001", role=UserRole.client, locale="en", client_org_id=org.id, display_name="Client Jane")
            u_worker = User(phone="+19993000002", role=UserRole.worker, locale="en")
            u_ta = User(phone="+19993000003", role=UserRole.ta, locale="en", display_name="TA Alex")
            s.add_all([u_client, u_worker, u_ta])
            await s.flush()
            wp = WorkerProfile(user_id=u_worker.id, full_name="Worker One")
            s.add(wp)
            await s.flush()
            job = JobOrder(
                client_org_id=org.id,
                created_by_user_id=u_client.id,
                title="Warehouse Shift",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.completed,
            )
            s.add(job)
            await s.flush()
            shift = Shift(job_order_id=job.id, starts_at=now, ends_at=now + timedelta(hours=8), workers_required=1)
            s.add(shift)
            await s.flush()
            assignment = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.completed)
            s.add(assignment)
            await s.flush()
            disputable_entry = TimesheetEntry(
                job_order_id=job.id,
                shift_id=shift.id,
                worker_id=wp.id,
                shift_assignment_id=assignment.id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=160.0,
                status=TimesheetStatus.ta_verified,
            )
            paid_entry = TimesheetEntry(
                job_order_id=job.id,
                shift_id=shift.id,
                worker_id=wp.id,
                shift_assignment_id=assignment.id,
                scheduled_hours=8.0,
                actual_hours=8.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=20.0,
                gross_pay=160.0,
                status=TimesheetStatus.payment_released,
            )
            s.add_all([disputable_entry, paid_entry])
            s.add(
                TaClientContract(
                    client_org_id=org.id,
                    ta_user_id=u_ta.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=u_client.id,
                )
            )
            await s.commit()
            return engine, Session, u_client.id, u_ta.id, u_worker.id, disputable_entry.id, paid_entry.id

    engine, Session, client_user_id, ta_user_id, worker_user_id, disputable_entry_id, paid_entry_id = asyncio.run(init())

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
        "client": create_access_token(str(client_user_id), {}),
        "ta": create_access_token(str(ta_user_id), {}),
        "worker": create_access_token(str(worker_user_id), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, Session, tokens, worker_user_id, disputable_entry_id, paid_entry_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_client_dispute_sets_status_and_creates_notification(dispute_setup):
    client, Session, tokens, worker_user_id, disputable_entry_id, _paid_entry_id = dispute_setup
    response = client.post(
        f"/timesheets/{disputable_entry_id}/dispute",
        headers={"Authorization": f"Bearer {tokens['client']}"},
        json={"note": "Clock-out mismatch needs review"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}

    async def check_state():
        async with Session() as s:
            entry = await s.get(TimesheetEntry, disputable_entry_id)
            notifications = (
                await s.execute(select(Notification).where(Notification.user_id == worker_user_id).order_by(Notification.id))
            ).scalars().all()
            return entry, notifications

    entry, notifications = asyncio.run(check_state())
    assert entry is not None
    assert entry.status == TimesheetStatus.disputed
    assert entry.dispute_flag is True
    assert entry.dispute_note == "Clock-out mismatch needs review"
    assert len(notifications) == 1
    assert notifications[0].title == "Timesheet disputed"
    assert "Warehouse Shift" in notifications[0].body
    assert notifications[0].payload["type"] == "timesheet_disputed"
    assert notifications[0].payload["timesheet_entry_id"] == disputable_entry_id


def test_dispute_requires_non_blank_comment(dispute_setup):
    client, Session, tokens, _worker_user_id, disputable_entry_id, _paid_entry_id = dispute_setup
    response = client.post(
        f"/timesheets/{disputable_entry_id}/dispute",
        headers={"Authorization": f"Bearer {tokens['client']}"},
        json={"note": "   "},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Dispute comment is required"

    async def check_entry():
        async with Session() as s:
            return await s.get(TimesheetEntry, disputable_entry_id)

    entry = asyncio.run(check_entry())
    assert entry is not None
    assert entry.status == TimesheetStatus.ta_verified
    assert entry.dispute_flag is False


def test_paid_timesheet_cannot_be_disputed(dispute_setup):
    client, _Session, tokens, _worker_user_id, _disputable_entry_id, paid_entry_id = dispute_setup
    response = client.post(
        f"/timesheets/{paid_entry_id}/dispute",
        headers={"Authorization": f"Bearer {tokens['client']}"},
        json={"note": "Too late to adjust"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Timesheet can no longer be disputed"


def test_ta_with_active_contract_can_dispute(dispute_setup):
    client, Session, tokens, _worker_user_id, disputable_entry_id, _paid_entry_id = dispute_setup
    response = client.post(
        f"/timesheets/{disputable_entry_id}/dispute",
        headers={"Authorization": f"Bearer {tokens['ta']}"},
        json={"note": "Manual payroll review requested"},
    )
    assert response.status_code == 200, response.text

    async def check_entry():
        async with Session() as s:
            return await s.get(TimesheetEntry, disputable_entry_id)

    entry = asyncio.run(check_entry())
    assert entry is not None
    assert entry.status == TimesheetStatus.disputed
    assert entry.dispute_note == "Manual payroll review requested"


def test_worker_list_timesheets_omits_disputed(dispute_setup):
    client, _Session, tokens, _worker_user_id, disputable_entry_id, paid_entry_id = dispute_setup
    worker_headers = {"Authorization": f"Bearer {tokens['worker']}"}

    before = client.get("/timesheets", headers=worker_headers)
    assert before.status_code == 200, before.text
    ids_before = {row["id"] for row in before.json()}
    assert disputable_entry_id in ids_before
    assert paid_entry_id in ids_before

    dispute = client.post(
        f"/timesheets/{disputable_entry_id}/dispute",
        headers={"Authorization": f"Bearer {tokens['client']}"},
        json={"note": "Hours look wrong"},
    )
    assert dispute.status_code == 200, dispute.text

    after = client.get("/timesheets", headers=worker_headers)
    assert after.status_code == 200, after.text
    ids_after = {row["id"] for row in after.json()}
    assert disputable_entry_id not in ids_after
    assert paid_entry_id in ids_after

    client_view = client.get("/timesheets", headers={"Authorization": f"Bearer {tokens['client']}"})
    assert client_view.status_code == 200, client_view.text
    assert disputable_entry_id in {row["id"] for row in client_view.json()}
