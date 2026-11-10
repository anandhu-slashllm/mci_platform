"""Automatic dispatch configuration and mode behavior."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AutomaticDispatchMode,
    ClientOrganization,
    DispatchTaskStatus,
    JobOrder,
    JobOrderStatus,
    Notification,
    PayModel,
    PreferredWorker,
    Shift,
    ShiftDispatchTask,
    ShiftOffer,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import job_orders as job_orders_router
from app.services.dispatch import dispatch_shift, process_dispatch_tasks_once
from app.services.matching import rank_workers_for_shift

_app = FastAPI()
_app.include_router(job_orders_router.router)


async def _seed():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+17770000001", role=UserRole.client, locale="en", client_org_id=org.id)
        session.add(client_user)
        await session.flush()

        workers: list[WorkerProfile] = []
        for idx in range(5):
            user = User(phone=f"+1777000001{idx}", role=UserRole.worker, locale="en")
            session.add(user)
            await session.flush()
            worker = WorkerProfile(
                user_id=user.id,
                full_name=f"Worker {idx}",
                location_lat=1.3,
                location_lng=103.8,
                rating_avg=5.0 - (idx * 0.2),
                completion_rate=0.9 - (idx * 0.1),
                job_type_preferences=["Warehouse Worker"],
                tier=WorkerTier.elite if idx == 0 else WorkerTier.standard,
                status=WorkerStatus.active,
            )
            workers.append(worker)
            session.add(worker)
        await session.flush()

        session.add(PreferredWorker(client_org_id=org.id, worker_id=workers[3].id))

        job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Night shift",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
            automatic_dispatch_enabled=True,
            automatic_dispatch_mode=AutomaticDispatchMode.fcfs,
            automatic_dispatch_min_score=0.6,
            automatic_dispatch_top_percent=20,
            automatic_dispatch_priority_window_minutes=60,
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=now + timedelta(hours=3),
            ends_at=now + timedelta(hours=11),
            workers_required=1,
        )
        session.add(shift)
        await session.commit()
        return engine, Session, client_user.id, job.id, shift.id


def test_job_order_auto_dispatch_config_roundtrip_and_validation():
    engine, Session, client_user_id, _, _ = asyncio.run(_seed())

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
            bad_payload = {
                "title": "Bad config",
                "job_type": "Warehouse Worker",
                "location_address": "SG",
                "location_lat": 1.3,
                "location_lng": 103.8,
                "workers_required_total": 1,
                "pay_model": "hourly",
                "automatic_dispatch_enabled": True,
                "automatic_dispatch_mode": "performance_priority",
                "shifts": [
                    {
                        "starts_at": "2026-04-22T09:00:00Z",
                        "ends_at": "2026-04-22T17:00:00Z",
                        "workers_required": 1,
                    }
                ],
            }
            bad = client.post("/job-orders", json=bad_payload, headers={"Authorization": f"Bearer {token}"})
            assert bad.status_code == 422, bad.text

            good_payload = {
                **bad_payload,
                "title": "Good config",
                "automatic_dispatch_priority_window_minutes": 120,
            }
            created = client.post("/job-orders", json=good_payload, headers={"Authorization": f"Bearer {token}"})
            assert created.status_code == 200, created.text
            data = created.json()
            assert data["automatic_dispatch_enabled"] is True
            assert data["automatic_dispatch_mode"] == "performance_priority"
            assert data["automatic_dispatch_top_percent"] is None
            assert data["automatic_dispatch_min_score"] is None
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_dispatch_modes_fcfs_performance_and_preferred():
    engine, Session, _, job_id, shift_id = asyncio.run(_seed())

    async def _run() -> None:
        async with Session() as session:
            job = await session.get(JobOrder, job_id)
            shift = await session.get(Shift, shift_id)
            assert job is not None
            assert shift is not None

            # FCFS notifies all eligible ranked workers.
            job.automatic_dispatch_mode = AutomaticDispatchMode.fcfs
            job.automatic_dispatch_min_score = None
            await dispatch_shift(session, job, shift)
            fcfs_offers = (
                await session.execute(select(ShiftOffer).where(ShiftOffer.shift_id == shift.id))
            ).scalars().all()
            assert len(fcfs_offers) >= 1

            # Performance mode: Elite workers first, then remainder when task is due.
            job2 = JobOrder(
                client_org_id=job.client_org_id,
                created_by_user_id=job.created_by_user_id,
                title="Perf mode",
                job_type=job.job_type,
                location_address=job.location_address,
                location_lat=job.location_lat,
                location_lng=job.location_lng,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.open,
                automatic_dispatch_enabled=True,
                automatic_dispatch_mode=AutomaticDispatchMode.performance_priority,
                automatic_dispatch_min_score=0.0,
                automatic_dispatch_top_percent=20,
                automatic_dispatch_priority_window_minutes=1,
            )
            session.add(job2)
            await session.flush()
            shift2 = Shift(
                job_order_id=job2.id,
                starts_at=shift.starts_at + timedelta(days=1),
                ends_at=shift.ends_at + timedelta(days=1),
                workers_required=1,
            )
            session.add(shift2)
            await session.flush()

            sent_phase_1 = await dispatch_shift(session, job2, shift2)
            assert sent_phase_1 == 1
            first_perf_offer = (
                await session.execute(
                    select(ShiftOffer).where(ShiftOffer.shift_id == shift2.id).order_by(ShiftOffer.id.asc())
                )
            ).scalars().first()
            assert first_perf_offer is not None
            elite_worker = await session.get(WorkerProfile, first_perf_offer.worker_id)
            assert elite_worker is not None
            assert elite_worker.tier == WorkerTier.elite

            phase1_notification = (
                await session.execute(
                    select(Notification)
                    .where(Notification.user_id == elite_worker.user_id)
                    .order_by(Notification.id.desc())
                )
            ).scalars().first()
            assert phase1_notification is not None
            assert "shift starting " in phase1_notification.body
            assert shift2.starts_at.isoformat() not in phase1_notification.body
            assert phase1_notification.body.endswith("UTC")

            task = (
                await session.execute(select(ShiftDispatchTask).where(ShiftDispatchTask.shift_id == shift2.id))
            ).scalar_one()
            task.run_after = datetime.now(timezone.utc) - timedelta(minutes=1)
            processed = await process_dispatch_tasks_once(session)
            assert processed == 1
            assert task.status == DispatchTaskStatus.completed
            perf_offers = (
                await session.execute(select(ShiftOffer).where(ShiftOffer.shift_id == shift2.id))
            ).scalars().all()
            assert len(perf_offers) == 1

            # Preferred priority gives preferred worker first notification.
            job3 = JobOrder(
                client_org_id=job.client_org_id,
                created_by_user_id=job.created_by_user_id,
                title="Preferred mode",
                job_type=job.job_type,
                location_address=job.location_address,
                location_lat=job.location_lat,
                location_lng=job.location_lng,
                workers_required_total=1,
                pay_model=PayModel.hourly,
                hourly_rate=20.0,
                status=JobOrderStatus.open,
                automatic_dispatch_enabled=True,
                automatic_dispatch_mode=AutomaticDispatchMode.preferred_priority,
                automatic_dispatch_min_score=0.0,
            )
            session.add(job3)
            await session.flush()
            shift3 = Shift(
                job_order_id=job3.id,
                starts_at=shift.starts_at + timedelta(days=2),
                ends_at=shift.ends_at + timedelta(days=2),
                workers_required=1,
            )
            session.add(shift3)
            await session.flush()
            pref_worker_id = (
                await session.execute(
                    select(PreferredWorker.worker_id).where(PreferredWorker.client_org_id == job3.client_org_id)
                )
            ).scalar_one()
            preferred_worker = await session.get(WorkerProfile, pref_worker_id)
            assert preferred_worker is not None
            # Make preferred worker ineligible under normal matching (weekday mismatch).
            preferred_worker.availability = {"weekdays": []}
            await session.flush()
            ranked_preferred_mode = await rank_workers_for_shift(session, job3, shift3)
            assert all(worker.id != pref_worker_id for worker, _score in ranked_preferred_mode)

            await dispatch_shift(session, job3, shift3)
            first_offer = (
                await session.execute(
                    select(ShiftOffer).where(ShiftOffer.shift_id == shift3.id).order_by(ShiftOffer.id.asc())
                )
            ).scalars().first()
            assert first_offer is not None
            assert first_offer.worker_id == pref_worker_id

            await session.commit()

    try:
        asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
