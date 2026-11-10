"""HTTP tests for worker report submission and admin report workflow."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

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
    User,
    UserRole,
    WorkerProfile,
    WorkerReportStatus,
    WorkerShiftReport,
    WorkerTier,
)
from app.routers import worker_reports as worker_reports_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(worker_reports_router.router)


@pytest.fixture
def report_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            client_org = ClientOrganization(name="Client Org")
            s.add(client_org)
            await s.flush()

            admin = User(phone="+1999000001", role=UserRole.admin, locale="en", is_active=True)
            ta = User(phone="+1999000002", role=UserRole.ta, locale="en", is_active=True, display_name="TA Amy")
            worker_user = User(phone="+1999000003", role=UserRole.worker, locale="en", is_active=True, email="w@example.com")
            other_worker_user = User(phone="+1999000004", role=UserRole.worker, locale="en", is_active=True)
            s.add_all([admin, ta, worker_user, other_worker_user])
            await s.flush()

            worker_profile = WorkerProfile(
                user_id=worker_user.id,
                full_name="Worker One",
                languages=["en"],
                job_type_preferences=["Warehouse Worker"],
                skills=[],
                certifications=[],
                availability={},
                tier=WorkerTier.standard,
            )
            other_worker_profile = WorkerProfile(
                user_id=other_worker_user.id,
                full_name="Worker Two",
                languages=["en"],
                job_type_preferences=["Warehouse Worker"],
                skills=[],
                certifications=[],
                availability={},
                tier=WorkerTier.standard,
            )
            s.add_all([worker_profile, other_worker_profile])
            await s.flush()

            s.add(
                TaClientContract(
                    client_org_id=client_org.id,
                    ta_user_id=ta.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=ta.id,
                )
            )
            await s.flush()

            job = JobOrder(
                client_org_id=client_org.id,
                created_by_user_id=ta.id,
                title="Loader Shift",
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
                starts_at=now + timedelta(hours=2),
                ends_at=now + timedelta(hours=10),
                workers_required=1,
            )
            s.add(shift)
            await s.flush()
            confirmed_assignment = ShiftAssignment(
                shift_id=shift.id,
                worker_id=worker_profile.id,
                status=AssignmentStatus.confirmed,
            )
            applied_assignment = ShiftAssignment(
                shift_id=shift.id,
                worker_id=other_worker_profile.id,
                status=AssignmentStatus.applied,
            )
            s.add_all([confirmed_assignment, applied_assignment])
            await s.commit()
            return engine, Session, admin.id, ta.id, worker_user.id, confirmed_assignment.id, applied_assignment.id

    engine, Session, admin_id, ta_id, worker_user_id, confirmed_assignment_id, applied_assignment_id = asyncio.run(init())

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
        "admin": create_access_token(str(admin_id), {}),
        "ta": create_access_token(str(ta_id), {}),
        "worker": create_access_token(str(worker_user_id), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, Session, tokens, confirmed_assignment_id, applied_assignment_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_worker_can_submit_report_when_confirmed(report_setup):
    client, _Session, tokens, confirmed_assignment_id, _applied_assignment_id = report_setup
    res = client.post(
        "/worker-reports",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
        json={"shift_assignment_id": confirmed_assignment_id, "reason": "Unsafe instruction"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True


def test_worker_cannot_submit_report_when_not_confirmed(report_setup):
    client, _Session, tokens, _confirmed_assignment_id, applied_assignment_id = report_setup
    res = client.post(
        "/worker-reports",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
        json={"shift_assignment_id": applied_assignment_id, "reason": "Bad shift"},
    )
    assert res.status_code in (403, 404), res.text


def test_admin_list_has_context_fields(report_setup):
    client, _Session, tokens, confirmed_assignment_id, _applied_assignment_id = report_setup
    create = client.post(
        "/worker-reports",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
        json={"shift_assignment_id": confirmed_assignment_id, "reason": "Safety concern"},
    )
    assert create.status_code == 200, create.text

    listed = client.get("/worker-reports/admin", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert row["job_order_name"] == "Loader Shift"
    assert row["worker_name"] == "Worker One"
    assert row["client_org_name"] == "Client Org"
    assert row["contracted_ta_name"] is not None


def test_non_admin_cannot_access_admin_report_endpoints(report_setup):
    client, _Session, tokens, confirmed_assignment_id, _applied_assignment_id = report_setup
    create = client.post(
        "/worker-reports",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
        json={"shift_assignment_id": confirmed_assignment_id, "reason": "Safety concern"},
    )
    assert create.status_code == 200, create.text
    report_id = create.json()["report_id"]

    l = client.get("/worker-reports/admin", headers={"Authorization": f"Bearer {tokens['ta']}"})
    assert l.status_code == 403

    u = client.patch(
        f"/worker-reports/admin/{report_id}/status",
        headers={"Authorization": f"Bearer {tokens['ta']}"},
        json={"status": "resolved"},
    )
    assert u.status_code == 403


def test_admin_status_update_notifies_worker(report_setup):
    client, Session, tokens, confirmed_assignment_id, _applied_assignment_id = report_setup
    create = client.post(
        "/worker-reports",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
        json={"shift_assignment_id": confirmed_assignment_id, "reason": "Safety concern"},
    )
    assert create.status_code == 200, create.text
    report_id = create.json()["report_id"]

    up = client.patch(
        f"/worker-reports/admin/{report_id}/status",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={"status": "under_review"},
    )
    assert up.status_code == 200, up.text

    async def verify():
        async with Session() as s:
            report = await s.get(WorkerShiftReport, report_id)
            assert report is not None
            assert report.status == WorkerReportStatus.under_review
            n = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == report.worker_user_id,
                        Notification.payload["type"].as_string() == "worker_report_status_updated",
                    )
                )
            ).scalar_one_or_none()
            assert n is not None
            assert "under_review" in n.body

    asyncio.run(verify())
