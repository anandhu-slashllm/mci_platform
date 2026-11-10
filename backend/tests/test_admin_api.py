"""HTTP tests for /admin PDPA deactivate, mark no-shows, and audit log listing."""

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
    AuditLog,
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
    WorkerStatus,
)
from app.routers import admin as admin_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(admin_router.router)


@pytest.fixture
def admin_setup():
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
            admin = User(phone="+16660001001", role=UserRole.admin, locale="en", is_active=True)
            ta = User(phone="+16660001002", role=UserRole.ta, locale="en", is_active=True, email="ta1@example.com")
            ta2 = User(phone="+16660001004", role=UserRole.ta, locale="en", is_active=False, email="ta2@example.com")
            client_user = User(
                phone="+16660001005",
                role=UserRole.client,
                locale="en",
                is_active=True,
                email="client@example.com",
            )
            worker = User(phone="+16660001003", role=UserRole.worker, locale="en", is_active=True, email="worker@example.com")
            s.add_all([admin, ta, ta2, client_user, worker])
            await s.flush()
            wp = WorkerProfile(
                user_id=worker.id,
                full_name="NS Worker",
                languages=["en"],
                job_type_preferences=["Warehouse Worker"],
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
                created_by_user_id=ta.id,
                title="Past shift",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=15.0,
                status=JobOrderStatus.open,
                no_show_grace_minutes=15,
            )
            s.add(job)
            await s.flush()
            shift = Shift(
                job_order_id=job.id,
                starts_at=now - timedelta(hours=3),
                ends_at=now - timedelta(hours=2),
                workers_required=1,
            )
            s.add(shift)
            await s.flush()
            asn = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.confirmed)
            s.add(asn)
            s.add(
                AuditLog(
                    actor_user_id=admin.id,
                    action="test_action",
                    entity_type="User",
                    entity_id=str(worker.id),
                )
            )
            await s.commit()
            return engine, Session, admin.id, ta.id, worker.id, shift.id

    engine, Session, admin_id, ta_id, worker_id, shift_id = asyncio.run(init())

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
        "worker": create_access_token(str(worker_id), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, Session, tokens, worker_id, shift_id
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_pdpa_deactivate_admin_only(admin_setup):
    client, _Session, tokens, worker_id, _shift_id = admin_setup
    r = client.post(
        f"/admin/pdpa/user/{worker_id}/deactivate",
        headers={"Authorization": f"Bearer {tokens['worker']}"},
    )
    assert r.status_code == 403

    r2 = client.post(
        f"/admin/pdpa/user/{worker_id}/deactivate",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


def test_pdpa_deactivate_unknown_user(admin_setup):
    client, _Session, tokens, _worker_id, _shift_id = admin_setup
    r = client.post(
        "/admin/pdpa/user/999999/deactivate",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 404


def test_mark_no_shows_ta_and_admin_allowed(admin_setup):
    client, Session, tokens, _worker_id, shift_id = admin_setup
    r = client.post(
        f"/admin/shifts/{shift_id}/mark-no-shows",
        headers={"Authorization": f"Bearer {tokens['ta']}"},
    )
    assert r.status_code == 200
    assert r.json()["marked"] >= 1

    async def check_assignment():
        async with Session() as s:
            r2 = await s.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == shift_id))
            a = r2.scalar_one()
            assert a.status == AssignmentStatus.no_show

    asyncio.run(check_assignment())


def test_audit_logs_admin_and_ta_not_worker(admin_setup):
    client, _Session, tokens, _worker_id, _shift_id = admin_setup
    rw = client.get("/admin/audit-logs", headers={"Authorization": f"Bearer {tokens['worker']}"})
    assert rw.status_code == 403

    ra = client.get("/admin/audit-logs", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert ra.status_code == 200
    assert isinstance(ra.json(), list)
    assert any(x.get("action") == "test_action" for x in ra.json())

    rt = client.get("/admin/audit-logs", headers={"Authorization": f"Bearer {tokens['ta']}"})
    assert rt.status_code == 200


def test_admin_persona_lists_show_all_statuses(admin_setup):
    client, _Session, tokens, worker_id, _shift_id = admin_setup
    rw = client.get("/admin/personas/worker", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert rw.status_code == 200, rw.text
    worker_row = next(x for x in rw.json() if x["user_id"] == worker_id)
    assert worker_row["status"] == "active"

    rc = client.get("/admin/personas/client", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert rc.status_code == 200, rc.text
    assert any(x["role"] == "client" for x in rc.json())

    rt = client.get("/admin/personas/ta", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert rt.status_code == 200, rt.text
    rows = rt.json()
    assert any(x["is_active"] is True for x in rows)
    assert any(x["is_active"] is False for x in rows)


def test_admin_blacklist_worker_disables_and_marks_status(admin_setup):
    client, Session, tokens, worker_id, _shift_id = admin_setup
    reason = "Repeated safety violations"
    res = client.post(
        f"/admin/personas/{worker_id}/blacklist",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={"reason": reason},
    )
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    list_res = client.get("/admin/personas/worker", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert list_res.status_code == 200, list_res.text
    worker_row = next(x for x in list_res.json() if x["user_id"] == worker_id)
    assert worker_row["blacklist_reason"] == reason

    async def check_user_and_worker():
        async with Session() as s:
            user = await s.get(User, worker_id)
            assert user is not None
            assert user.is_active is False
            wr = await s.execute(select(WorkerProfile).where(WorkerProfile.user_id == worker_id))
            wp = wr.scalar_one()
            assert wp.status == WorkerStatus.blacklisted

    asyncio.run(check_user_and_worker())


def test_admin_blacklist_requires_reason(admin_setup):
    client, _Session, tokens, worker_id, _shift_id = admin_setup
    res = client.post(f"/admin/personas/{worker_id}/blacklist", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert res.status_code == 422, res.text


def test_admin_can_unblacklist_ta_and_client(admin_setup):
    client, Session, tokens, _worker_id, _shift_id = admin_setup

    async def get_ta_and_client():
        async with Session() as s:
            ta = (await s.execute(select(User).where(User.email == "ta1@example.com"))).scalar_one()
            client_user = (await s.execute(select(User).where(User.email == "client@example.com"))).scalar_one()
            return ta.id, client_user.id

    ta_id, client_id = asyncio.run(get_ta_and_client())
    for target_id in (ta_id, client_id):
        bl = client.post(
            f"/admin/personas/{target_id}/blacklist",
            headers={"Authorization": f"Bearer {tokens['admin']}"},
            json={"reason": "Policy breach"},
        )
        assert bl.status_code == 200, bl.text
        un = client.post(f"/admin/personas/{target_id}/unblacklist", headers={"Authorization": f"Bearer {tokens['admin']}"})
        assert un.status_code == 200, un.text

    async def verify_active():
        async with Session() as s:
            ta = await s.get(User, ta_id)
            client_user = await s.get(User, client_id)
            assert ta is not None and ta.is_active is True
            assert client_user is not None and client_user.is_active is True

    asyncio.run(verify_active())
