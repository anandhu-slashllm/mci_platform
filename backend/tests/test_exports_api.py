"""HTTP tests for /exports CSV/XLSX role gates and TA org scoping."""

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
    TaClientContract,
    TaClientContractStatus,
    TimesheetEntry,
    TimesheetStatus,
    User,
    UserRole,
    WorkerProfile,
)
from app.routers import exports as exports_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(exports_router.router)


@pytest.fixture
def exports_setup():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as s:
            org = ClientOrganization(name="Export Org")
            s.add(org)
            await s.flush()
            ta = User(phone="+18880003001", role=UserRole.ta, locale="en")
            ta2 = User(phone="+18880003002", role=UserRole.ta, locale="en")
            admin = User(phone="+18880003003", role=UserRole.admin, locale="en")
            client_u = User(
                phone="+18880003004",
                role=UserRole.client,
                locale="en",
                client_org_id=org.id,
            )
            worker_u = User(phone="+18880003005", role=UserRole.worker, locale="en")
            s.add_all([ta, ta2, admin, client_u, worker_u])
            await s.flush()
            wp = WorkerProfile(user_id=worker_u.id, full_name="Pay Worker")
            s.add(wp)
            await s.flush()
            job = JobOrder(
                client_org_id=org.id,
                created_by_user_id=client_u.id,
                title="Job A",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=12.0,
                status=JobOrderStatus.open,
            )
            s.add(job)
            await s.flush()
            shift = Shift(
                job_order_id=job.id,
                starts_at=now,
                ends_at=now + timedelta(hours=4),
                workers_required=1,
            )
            s.add(shift)
            await s.flush()
            asn = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.completed)
            s.add(asn)
            await s.flush()
            entry = TimesheetEntry(
                job_order_id=job.id,
                shift_id=shift.id,
                worker_id=wp.id,
                shift_assignment_id=asn.id,
                scheduled_hours=4.0,
                actual_hours=4.0,
                overtime_hours=0.0,
                hourly_rate_snapshot=12.0,
                gross_pay=48.0,
                status=TimesheetStatus.ta_verified,
            )
            s.add(entry)
            await s.flush()
            s.add(
                TaClientContract(
                    client_org_id=org.id,
                    ta_user_id=ta.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=client_u.id,
                )
            )
            await s.commit()
            return engine, Session, ta.id, ta2.id, admin.id, client_u.id, worker_u.id, entry.id

    engine, Session, ta_id, ta2_id, admin_id, client_id, worker_id, entry_id = asyncio.run(init())

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
        "ta": create_access_token(str(ta_id), {}),
        "ta_other": create_access_token(str(ta2_id), {}),
        "admin": create_access_token(str(admin_id), {}),
        "client": create_access_token(str(client_id), {}),
        "worker": create_access_token(str(worker_id), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, tokens, entry_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_exports_worker_forbidden(exports_setup):
    client, tokens, _eid = exports_setup
    r = client.get("/exports/zoho/job-orders.csv", headers={"Authorization": f"Bearer {tokens['worker']}"})
    assert r.status_code == 403


def test_exports_ta_without_contract_gets_empty_job_orders_csv(exports_setup):
    client, tokens, _eid = exports_setup
    r = client.get("/exports/zoho/job-orders.csv", headers={"Authorization": f"Bearer {tokens['ta_other']}"})
    assert r.status_code == 200
    text = r.text
    assert "Deal_Name" in text
    assert text.count("\n") <= 2


def test_exports_client_payroll_lines_ok(exports_setup):
    client, tokens, _eid = exports_setup
    r = client.get("/exports/payroll/client-lines.csv", headers={"Authorization": f"Bearer {tokens['client']}"})
    assert r.status_code == 200
    assert "timesheet_entry_id" in r.text


def test_exports_ta_single_line_404_without_contract(exports_setup):
    client, tokens, entry_id = exports_setup
    r = client.get(
        f"/exports/payroll/ta-verified-lines/{entry_id}.csv",
        headers={"Authorization": f"Bearer {tokens['ta_other']}"},
    )
    assert r.status_code == 404


def test_exports_ta_single_line_ok_with_contract(exports_setup):
    client, tokens, entry_id = exports_setup
    r = client.get(
        f"/exports/payroll/ta-verified-lines/{entry_id}.csv",
        headers={"Authorization": f"Bearer {tokens['ta']}"},
    )
    assert r.status_code == 200
    assert "timesheet_entry_id" in r.text


def test_exports_zoho_xlsx_content_type(exports_setup):
    client, tokens, _eid = exports_setup
    r = client.get("/exports/zoho/job-orders.xlsx", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200
    assert "spreadsheet" in r.headers.get("content-type", "")
