"""Job-order match-weight defaults and overrides surface through the API."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

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
    JobTypeWeight,
    PayModel,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import job_orders as job_orders_router

get_settings.cache_clear()

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
        worker_user = User(phone="+17770000002", role=UserRole.worker, locale="en")
        session.add_all([client_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Worker One",
            location_lat=1.31,
            location_lng=103.81,
            rating_avg=4.8,
            job_type_preferences=[],
            completion_rate=0.35,
            certifications=["forklift"],
            skills=["loading"],
            tier=WorkerTier.standard,
            status=WorkerStatus.active,
        )
        session.add(worker)
        session.add(
            JobTypeWeight(
                job_type="Warehouse Worker",
                weight_proximity=0.05,
                weight_rating=0.6,
                weight_job_type=0.05,
                weight_completion=0.2,
                weight_tier=0.1,
            )
        )
        await session.flush()

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
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=now,
            ends_at=now + timedelta(hours=8),
            workers_required=1,
        )
        session.add(shift)
        await session.flush()

        session.add(
            ShiftAssignment(
                shift_id=shift.id,
                worker_id=worker.id,
                status=AssignmentStatus.applied,
            )
        )
        await session.commit()
        return engine, Session, client_user.id, job.id


def test_job_order_match_weights_default_and_override():
    engine, Session, client_user_id, job_id = asyncio.run(_seed())

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
            r_default = client.get(
                "/job-orders/match-weights/defaults",
                params={"job_type": "Warehouse Worker"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_default.status_code == 200, r_default.text
            assert r_default.json()["weight_rating"] == 0.6

            r_detail = client.get(f"/job-orders/{job_id}", headers={"Authorization": f"Bearer {token}"})
            assert r_detail.status_code == 200, r_detail.text
            detail = r_detail.json()
            assert detail["match_weight_overrides"] is None
            assert detail["match_weights"]["weight_rating"] == 0.6
            baseline_score = detail["accepted_workers"][0]["match_score"]
            assert baseline_score is not None

            override_payload = {
                "match_weights": {
                    "weight_proximity": 0.7,
                    "weight_rating": 0.05,
                    "weight_job_type": 0.05,
                    "weight_completion": 0.1,
                    "weight_tier": 0.1,
                }
            }
            r_patch = client.patch(
                f"/job-orders/{job_id}/match-weights",
                json=override_payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_patch.status_code == 200, r_patch.text
            updated = r_patch.json()
            assert updated["match_weight_overrides"] == override_payload["match_weights"]
            assert updated["accepted_workers"][0]["match_score"] != baseline_score
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
