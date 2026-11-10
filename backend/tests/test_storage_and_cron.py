"""Supabase Storage-backed worker documents, cron endpoints, and DB-backed reminder idempotency."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    Document,
    JobOrder,
    JobOrderStatus,
    PayModel,
    Shift,
    ShiftAssignment,
    ShiftReminderSent,
    User,
    UserRole,
    WorkerProfile,
)
from app.routers import cron as cron_router
from app.routers import workers as workers_router
from app.services import shift_reminders
from auth_helpers import create_access_token

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(workers_router.router)
_app.include_router(cron_router.router)


@pytest.fixture
def ctx():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as s:
            ids = {}
            for key in ("w1", "w2"):
                u = User(email=f"{key}@example.com", role=UserRole.worker, locale="en")
                s.add(u)
                await s.flush()
                wp = WorkerProfile(user_id=u.id, full_name=key)
                s.add(wp)
                await s.flush()
                ids[key] = (u.id, wp.id)
            await s.commit()
        return engine, Session, ids

    engine, Session, ids = asyncio.run(init())

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    with TestClient(_app) as tc:
        yield tc, Session, ids
    _app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _h(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


# --- documents in object storage ----------------------------------------------------------------


def test_document_upload_goes_to_storage_and_signed_url_is_owner_only(ctx, memory_storage):
    tc, Session, ids = ctx
    w1_user, w1_wp = ids["w1"]
    w2_user, _ = ids["w2"]

    r = tc.post(
        "/workers/me/documents?doc_type=work_pass",
        headers=_h(w1_user),
        files={"file": ("pass.PNG", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["path"].startswith(f"storage:documents/worker-{w1_wp}/")
    [key] = memory_storage.objects
    assert key.endswith(".png")
    assert memory_storage.objects[key] == (b"\x89PNG fake", "image/png")

    docs = tc.get("/workers/me/documents", headers=_h(w1_user)).json()
    assert docs[0]["storage_object_path"] == key
    doc_id = docs[0]["id"]

    signed = tc.get(f"/workers/me/documents/{doc_id}/signed-url", headers=_h(w1_user))
    assert signed.status_code == 200
    assert signed.json()["url"].startswith(f"memory://{memory_storage.bucket}/{key}")

    # Another worker cannot get a link to it.
    assert tc.get(f"/workers/me/documents/{doc_id}/signed-url", headers=_h(w2_user)).status_code == 404


def test_document_upload_rejects_bad_type_and_oversize(ctx, memory_storage, monkeypatch):
    tc, _Session, ids = ctx
    w1_user, _ = ids["w1"]
    r = tc.post(
        "/workers/me/documents?doc_type=x",
        headers=_h(w1_user),
        files={"file": ("a.exe", b"MZ", "application/x-msdownload")},
    )
    assert r.status_code == 400
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 4)
    r = tc.post(
        "/workers/me/documents?doc_type=x", headers=_h(w1_user), files={"file": ("a.png", b"12345", "image/png")}
    )
    assert r.status_code == 413
    assert memory_storage.objects == {}


def test_legacy_local_disk_document_reports_gone(ctx):
    tc, Session, ids = ctx
    w1_user, w1_wp = ids["w1"]

    async def add_legacy():
        async with Session() as s:
            d = Document(worker_id=w1_wp, doc_type="old", file_path="uploads/old.jpg")
            s.add(d)
            await s.commit()
            return d.id

    doc_id = asyncio.run(add_legacy())
    assert tc.get(f"/workers/me/documents/{doc_id}/signed-url", headers=_h(w1_user)).status_code == 410


# --- cron endpoints -----------------------------------------------------------------------------


def test_cron_requires_configured_secret(ctx, monkeypatch):
    tc, _Session, _ids = ctx
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert tc.get("/internal/cron/tick").status_code == 503

    monkeypatch.setattr(get_settings(), "cron_secret", "s3cret")
    assert tc.get("/internal/cron/tick").status_code == 401
    assert tc.get("/internal/cron/tick", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert tc.get("/internal/cron/daily", headers={"Authorization": "s3cret"}).status_code == 401


def test_cron_tick_and_daily_run_jobs(ctx, monkeypatch):
    tc, _Session, _ids = ctx
    monkeypatch.setattr(get_settings(), "cron_secret", "s3cret")
    calls = []

    async def fake_tick():
        calls.append("tick")
        return {"dispatch_tasks_processed": 0}

    async def fake_refresh(*, run_daily=None):
        calls.append(("refresh", run_daily))
        return {"processed_workers": 0, "daily_refreshed": bool(run_daily)}

    monkeypatch.setattr(cron_router, "run_tick", fake_tick)
    monkeypatch.setattr(cron_router, "process_recommendation_refresh_once", fake_refresh)
    auth = {"Authorization": "Bearer s3cret"}
    assert tc.get("/internal/cron/tick", headers=auth).status_code == 200
    r = tc.post("/internal/cron/daily", headers=auth)
    assert r.status_code == 200 and r.json()["daily_refreshed"] is True
    assert calls == ["tick", ("refresh", True)]


# --- reminder idempotency -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shift_reminder_marked_once_per_kind():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        org = ClientOrganization(name="O")
        s.add(org)
        await s.flush()
        cu = User(email="c@x.com", role=UserRole.client, locale="en", client_org_id=org.id)
        wu = User(email="w@x.com", role=UserRole.worker, locale="en")
        s.add_all([cu, wu])
        await s.flush()
        wp = WorkerProfile(user_id=wu.id)
        job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=cu.id,
            title="J",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.3,
            location_lng=103.8,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=10,
            status=JobOrderStatus.open,
        )
        s.add_all([wp, job])
        await s.flush()
        now = datetime.now(timezone.utc)
        shift = Shift(job_order_id=job.id, starts_at=now + timedelta(hours=24), ends_at=now + timedelta(hours=30))
        s.add(shift)
        await s.flush()
        a = ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.confirmed)
        s.add(a)
        await s.flush()

        assert await shift_reminders._mark_sent(s, a.id, "24h") is True
        assert await shift_reminders._mark_sent(s, a.id, "24h") is False
        assert await shift_reminders._mark_sent(s, a.id, "2h") is True
        await s.commit()
        rows = (await s.execute(select(ShiftReminderSent.kind).order_by(ShiftReminderSent.kind))).scalars().all()
        assert rows == ["24h", "2h"]
    await engine.dispose()
