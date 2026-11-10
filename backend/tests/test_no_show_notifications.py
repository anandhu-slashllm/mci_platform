"""Stakeholder notifications when a worker is marked as no-show."""

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

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
    WorkerStatus,
    WorkerTier,
)
from app.routers import admin as admin_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(admin_router.router)


async def _notifications_for_users(Session, *user_ids: int) -> list[Notification]:
    async with Session() as session:
        return (
            await session.execute(
                select(Notification)
                .where(Notification.user_id.in_(user_ids))
                .order_by(Notification.id.asc())
            )
        ).scalars().all()


async def _seed(active_contract: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+18882110001", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_user = User(phone="+18882110002", role=UserRole.ta, locale="en")
        worker_user = User(phone="+18882110003", role=UserRole.worker, locale="en")
        session.add_all([client_user, ta_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="No Show Worker",
            languages=[],
            job_type_preferences=[],
            skills=[],
            certifications=[],
            availability={},
            tier=WorkerTier.standard,
            status=WorkerStatus.active,
        )
        session.add(worker)
        await session.flush()

        job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Dock shift",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.0,
            location_lng=103.0,
            geofence_radius_m=5000,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
            no_show_grace_minutes=10,
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=now - timedelta(hours=2),
            ends_at=now + timedelta(hours=6),
            workers_required=1,
        )
        session.add(shift)
        await session.flush()

        assignment = ShiftAssignment(
            shift_id=shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        session.add(assignment)

        if active_contract:
            session.add(
                TaClientContract(
                    client_org_id=org.id,
                    ta_user_id=ta_user.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=client_user.id,
                    requested_at=now - timedelta(days=1),
                    responded_at=now - timedelta(hours=12),
                )
            )

        await session.commit()
        return engine, Session, {
            "client_user_id": client_user.id,
            "ta_user_id": ta_user.id,
            "worker_user_id": worker_user.id,
            "shift_id": shift.id,
            "ta_token": create_access_token(str(ta_user.id), {}),
        }


@contextmanager
def _admin_client(seed_coro):
    engine, Session, ids = asyncio.run(seed_coro)

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(_app) as client:
            yield client, Session, ids
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_mark_no_shows_without_contract_notifies_client_only():
    with _admin_client(_seed(active_contract=False)) as (client, Session, ids):
        resp = client.post(
            f"/admin/shifts/{ids['shift_id']}/mark-no-shows",
            headers={"Authorization": f"Bearer {ids['ta_token']}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["marked"] == 1

        notes = asyncio.run(
            _notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"], ids["worker_user_id"])
        )
        client_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]
        worker_notes = [n for n in notes if n.user_id == ids["worker_user_id"]]

        assert len(client_notes) == 1
        assert client_notes[0].payload.get("type") == "worker_no_show"
        assert len(ta_notes) == 0
        assert len(worker_notes) == 1
        assert worker_notes[0].title == "Marked as no-show"


def test_mark_no_shows_with_contract_notifies_client_and_ta():
    with _admin_client(_seed(active_contract=True)) as (client, Session, ids):
        resp = client.post(
            f"/admin/shifts/{ids['shift_id']}/mark-no-shows",
            headers={"Authorization": f"Bearer {ids['ta_token']}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["marked"] == 1

        notes = asyncio.run(
            _notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"], ids["worker_user_id"])
        )
        client_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]
        worker_notes = [n for n in notes if n.user_id == ids["worker_user_id"]]

        assert len(client_notes) == 1
        assert len(ta_notes) == 1
        assert len(worker_notes) == 1
        assert client_notes[0].payload.get("type") == "worker_no_show"
        assert ta_notes[0].payload.get("type") == "worker_no_show"
        assert "No Show Worker" in client_notes[0].body
        assert "No Show Worker" in ta_notes[0].body
