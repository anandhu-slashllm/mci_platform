"""Stakeholder notifications for worker clock events and geofence mismatches."""

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
from app.routers import attendance as attendance_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(attendance_router.router)


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

        client_user = User(phone="+18881110001", role=UserRole.client, locale="en", client_org_id=org.id)
        client_user_2 = User(phone="+18881110004", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_user = User(phone="+18881110002", role=UserRole.ta, locale="en")
        worker_user = User(phone="+18881110003", role=UserRole.worker, locale="en")
        session.add_all([client_user, client_user_2, ta_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Alex Worker",
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
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(hours=8),
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
            "client_user_2_id": client_user_2.id,
            "ta_user_id": ta_user.id,
            "worker_user_id": worker_user.id,
            "job_id": job.id,
            "shift_id": shift.id,
            "shift_ends_at": shift.ends_at,
        }


@contextmanager
def _clock_client(seed_coro):
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
    token = create_access_token(str(ids["worker_user_id"]), {})
    try:
        with TestClient(_app) as client:
            yield client, Session, token, ids
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_clock_in_out_without_ta_contract_notifies_client_only():
    with _clock_client(_seed(active_contract=False)) as (client, Session, token, ids):
        qr_in = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="in",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_in = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_in, "lat": 1.0, "lng": 103.0},
        )
        assert r_in.status_code == 200, r_in.text

        qr_out = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="out",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_out = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_out, "lat": 1.0, "lng": 103.0},
        )
        assert r_out.status_code == 200, r_out.text

        notes = asyncio.run(
            _notifications_for_users(
                Session,
                ids["client_user_id"],
                ids["client_user_2_id"],
                ids["ta_user_id"],
            )
        )
        client_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        client2_notes = [n for n in notes if n.user_id == ids["client_user_2_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]
        assert len(client_notes) == 2
        assert len(client2_notes) == 2
        assert {n.payload["type"] for n in client_notes} == {"worker_clock_in", "worker_clock_out"}
        assert {n.payload["type"] for n in client2_notes} == {"worker_clock_in", "worker_clock_out"}
        assert all(n.payload["job_order_id"] == ids["job_id"] for n in client_notes)
        assert ta_notes == []


def test_clock_in_out_with_active_ta_contract_notifies_client_and_ta():
    with _clock_client(_seed(active_contract=True)) as (client, Session, token, ids):
        qr_in = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="in",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_in = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_in, "lat": 1.0, "lng": 103.0},
        )
        assert r_in.status_code == 200, r_in.text

        qr_out = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="out",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_out = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_out, "lat": 1.0, "lng": 103.0},
        )
        assert r_out.status_code == 200, r_out.text

        notes = asyncio.run(
            _notifications_for_users(
                Session,
                ids["client_user_id"],
                ids["client_user_2_id"],
                ids["ta_user_id"],
            )
        )
        client_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        client2_notes = [n for n in notes if n.user_id == ids["client_user_2_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]
        assert len(client_notes) == 2
        assert len(client2_notes) == 2
        assert len(ta_notes) == 2
        assert {n.payload["type"] for n in client_notes} == {"worker_clock_in", "worker_clock_out"}
        assert {n.payload["type"] for n in client2_notes} == {"worker_clock_in", "worker_clock_out"}
        assert {n.payload["type"] for n in ta_notes} == {"worker_clock_in", "worker_clock_out"}
        assert all("Alex Worker" in n.body for n in notes)


def test_geofence_clock_in_mismatch_notifies_job_creator_and_contracted_ta_only():
    with _clock_client(_seed(active_contract=True)) as (client, Session, token, ids):
        qr_in = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="in",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_in = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_in, "lat": 0.0, "lng": 0.0},
        )
        assert r_in.status_code == 400, r_in.text
        detail = r_in.json()["detail"]
        assert detail["code"] == "geofence_clock_in_blocked"

        notes = asyncio.run(
            _notifications_for_users(
                Session,
                ids["client_user_id"],
                ids["client_user_2_id"],
                ids["ta_user_id"],
            )
        )
        creator_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        other_client_notes = [n for n in notes if n.user_id == ids["client_user_2_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]

        assert len(creator_notes) == 1
        assert len(ta_notes) == 1
        assert other_client_notes == []
        assert creator_notes[0].payload["type"] == "worker_geofence_mismatch"
        assert creator_notes[0].payload["event"] == "clock_in"
        assert ta_notes[0].payload["type"] == "worker_geofence_mismatch"
        assert ta_notes[0].payload["event"] == "clock_in"


def test_geofence_clock_out_mismatch_notifies_job_creator_and_contracted_ta_only():
    with _clock_client(_seed(active_contract=True)) as (client, Session, token, ids):
        qr_in = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="in",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_in = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_in, "lat": 1.0, "lng": 103.0},
        )
        assert r_in.status_code == 200, r_in.text

        qr_out = create_attendance_qr_token(
            job_id=ids["job_id"],
            shift_id=ids["shift_id"],
            action="out",
            shift_ends_at=ids["shift_ends_at"],
        )
        r_out = client.post(
            "/attendance/qr-clock",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": qr_out, "lat": 0.0, "lng": 0.0},
        )
        assert r_out.status_code == 200, r_out.text
        assert r_out.json()["geofence_valid"] is False

        notes = asyncio.run(
            _notifications_for_users(
                Session,
                ids["client_user_id"],
                ids["client_user_2_id"],
                ids["ta_user_id"],
            )
        )
        creator_notes = [n for n in notes if n.user_id == ids["client_user_id"]]
        other_client_notes = [n for n in notes if n.user_id == ids["client_user_2_id"]]
        ta_notes = [n for n in notes if n.user_id == ids["ta_user_id"]]

        # creator and TA each get normal clock-in + mismatch clock-out
        assert len(creator_notes) == 2
        assert len(ta_notes) == 2
        assert len(other_client_notes) == 1
        assert other_client_notes[0].payload["type"] == "worker_clock_in"
        assert {n.payload["type"] for n in creator_notes} == {
            "worker_clock_in",
            "worker_geofence_mismatch",
        }
        assert {n.payload["type"] for n in ta_notes} == {
            "worker_clock_in",
            "worker_geofence_mismatch",
        }
        mismatch_creator = next(n for n in creator_notes if n.payload["type"] == "worker_geofence_mismatch")
        mismatch_ta = next(n for n in ta_notes if n.payload["type"] == "worker_geofence_mismatch")
        assert mismatch_creator.payload["event"] == "clock_out"
        assert mismatch_ta.payload["event"] == "clock_out"
