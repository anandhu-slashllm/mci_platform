"""Auth API on top of Supabase Auth (faked in-memory): password login, email OTP, refresh, logout,
and verification of Supabase access tokens (HS256 secret and ES256/JWKS)."""

import asyncio
import base64
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import supabase_jwt
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import ClientOrganization, ClientProfile, User, UserRole, WorkerProfile, WorkerStatus
from app.routers import auth as auth_router
from app.routers import notifications as notifications_router
from auth_helpers import mint_token

pytestmark = pytest.mark.no_auto_auth_link

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(auth_router.router)
_app.include_router(notifications_router.router)


@pytest.fixture
def auth_client():
    async def init():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine

    engine = asyncio.run(init())
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
        yield tc, Session
    _app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _add_user(Session, fake_auth, *, password: str | None = "pw", link: bool = True, worker_status=None, **fields):
    """Insert an app user and (optionally) its Supabase Auth identity."""

    async def run():
        async with Session() as s:
            fields.setdefault("is_active", True)
            u = User(locale="en", **fields)
            s.add(u)
            await s.flush()
            if u.role == UserRole.worker:
                s.add(WorkerProfile(user_id=u.id, status=worker_status or WorkerStatus.active))
            if link:
                email = u.email or f"{u.username}@users.mci-platform.local"
                u.supabase_user_id = fake_auth.add_user(email, password=password)
            await s.commit()
            return u.id, u.supabase_user_id

    return asyncio.run(run())


def _me(client, token):
    return client.get("/notifications", headers={"Authorization": f"Bearer {token}"})


# --- password login -----------------------------------------------------------------------------


def test_login_success_returns_supabase_session_usable_on_api(auth_client, fake_auth):
    client, Session = auth_client
    uid, auth_uid = _add_user(Session, fake_auth, username="ta_ops", role=UserRole.ta, password="correct-horse")

    r = client.post("/auth/login", json={"username": "ta_ops", "password": "correct-horse"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == uid and body["role"] == "ta"
    assert body["refresh_token"] and body["expires_at"] > 0

    claims = jose_jwt.get_unverified_claims(body["access_token"])
    assert claims["sub"] == auth_uid
    # app_metadata was stamped on first login so the frontend can route without an extra call.
    assert claims["app_metadata"] == {"mci_user_id": uid, "mci_role": "ta"}
    assert _me(client, body["access_token"]).status_code == 200


@pytest.mark.parametrize(
    "username,password",
    [("u1", "wrong"), ("   ", "x"), ("nobody", "x")],
)
def test_login_rejects_bad_credentials(auth_client, fake_auth, username, password):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="u1", role=UserRole.worker, password="secret")
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password"


def test_login_inactive_user(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="inactive", role=UserRole.ta, password="x", is_active=False)
    assert client.post("/auth/login", json={"username": "inactive", "password": "x"}).status_code == 401


def test_login_blacklisted_worker(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(
        Session, fake_auth, username="bl", role=UserRole.worker, password="x", worker_status=WorkerStatus.blacklisted
    )
    assert client.post("/auth/login", json={"username": "bl", "password": "x"}).status_code == 401


def test_login_user_not_linked_to_supabase(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="legacy", role=UserRole.ta, link=False)
    assert client.post("/auth/login", json={"username": "legacy", "password": "pw"}).status_code == 401


def test_login_otp_only_account_has_no_password(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="otp_only", role=UserRole.worker, password=None)
    assert client.post("/auth/login", json={"username": "otp_only", "password": "anything"}).status_code == 401


# --- refresh / logout ---------------------------------------------------------------------------


def test_refresh_rotates_session(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="ta1", role=UserRole.ta, password="pw")
    first = client.post("/auth/login", json={"username": "ta1", "password": "pw"}).json()

    r = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert _me(client, second["access_token"]).status_code == 200
    # Refresh tokens are single use.
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401


def test_refresh_denied_after_deactivation(auth_client, fake_auth):
    client, Session = auth_client
    uid, _ = _add_user(Session, fake_auth, username="ta2", role=UserRole.ta, password="pw")
    sess = client.post("/auth/login", json={"username": "ta2", "password": "pw"}).json()

    async def deactivate():
        async with Session() as s:
            (await s.get(User, uid)).is_active = False
            await s.commit()

    asyncio.run(deactivate())
    assert client.post("/auth/refresh", json={"refresh_token": sess["refresh_token"]}).status_code == 401
    assert _me(client, sess["access_token"]).status_code == 401


def test_logout_revokes_with_supabase(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, username="ta3", role=UserRole.ta, password="pw")
    tok = client.post("/auth/login", json={"username": "ta3", "password": "pw"}).json()["access_token"]
    r = client.post("/auth/logout", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 204
    assert fake_auth.signed_out == [tok]


# --- email OTP ----------------------------------------------------------------------------------


def test_email_otp_signup_creates_and_links_worker(auth_client, fake_auth):
    client, Session = auth_client
    r1 = client.post("/auth/worker/email-otp/request", json={"email": "New.Worker@Example.com", "locale": "zh"})
    assert r1.status_code == 200
    assert r1.json() == {"ok": True, "message": "Verification code sent", "dev_code": None, "delivery": "email"}
    assert fake_auth.otp_sent_to == ["new.worker@example.com"]

    r2 = client.post(
        "/auth/worker/email-otp/verify", json={"email": "new.worker@example.com", "code": "123456", "locale": "zh"}
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    claims = jose_jwt.get_unverified_claims(body["access_token"])
    assert claims["app_metadata"]["mci_role"] == "worker"
    assert claims["app_metadata"]["mci_user_id"] == body["user_id"]

    async def check():
        async with Session() as s:
            u = await s.get(User, body["user_id"])
            assert u.supabase_user_id == claims["sub"]
            assert u.locale == "zh" and u.email == "new.worker@example.com"
            assert (await s.execute(select(WorkerProfile).where(WorkerProfile.user_id == u.id))).scalar_one()

    asyncio.run(check())


def test_email_otp_links_existing_unlinked_user_by_email(auth_client, fake_auth):
    client, Session = auth_client
    uid, _ = _add_user(Session, fake_auth, email="legacy@example.com", role=UserRole.worker, link=False)
    client.post("/auth/worker/email-otp/request", json={"email": "legacy@example.com", "login_only": True})
    r = client.post(
        "/auth/worker/email-otp/verify", json={"email": "legacy@example.com", "code": "123456", "login_only": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == uid


def test_email_otp_wrong_code(auth_client, fake_auth):
    client, _ = auth_client
    client.post("/auth/worker/email-otp/request", json={"email": "new.worker@example.com"})
    r = client.post("/auth/worker/email-otp/verify", json={"email": "new.worker@example.com", "code": "000002"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired code"


def test_email_otp_conflict_when_email_is_client(auth_client, fake_auth):
    client, Session = auth_client

    async def seed():
        async with Session() as s:
            org = ClientOrganization(name="Co")
            s.add(org)
            await s.flush()
            u = User(email="same@example.com", role=UserRole.client, locale="en", client_org_id=org.id)
            s.add(u)
            await s.flush()
            s.add(ClientProfile(user_id=u.id, company_name="Co", contact_email="same@example.com"))
            await s.commit()

    asyncio.run(seed())
    client.post("/auth/worker/email-otp/request", json={"email": "same@example.com"})
    r = client.post("/auth/worker/email-otp/verify", json={"email": "same@example.com", "code": "123456"})
    assert r.status_code == 409


def test_email_otp_conflict_when_email_linked_to_other_auth_user(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(Session, fake_auth, email="dup@example.com", role=UserRole.worker, link=False)

    async def link_elsewhere():
        async with Session() as s:
            u = (await s.execute(select(User).where(User.email == "dup@example.com"))).scalar_one()
            u.supabase_user_id = "00000000-0000-0000-0000-000000000001"
            await s.commit()

    asyncio.run(link_elsewhere())
    client.post("/auth/worker/email-otp/request", json={"email": "dup@example.com"})
    r = client.post("/auth/worker/email-otp/verify", json={"email": "dup@example.com", "code": "123456"})
    assert r.status_code == 409


def test_email_otp_rejects_inactive_worker_profile(auth_client, fake_auth):
    client, Session = auth_client
    _add_user(
        Session,
        fake_auth,
        email="inactive.wp@example.com",
        role=UserRole.worker,
        link=False,
        worker_status=WorkerStatus.inactive,
    )
    client.post("/auth/worker/email-otp/request", json={"email": "inactive.wp@example.com"})
    r = client.post("/auth/worker/email-otp/verify", json={"email": "inactive.wp@example.com", "code": "123456"})
    assert r.status_code == 403


@pytest.mark.parametrize("persona", ["worker", "client"])
def test_email_login_only_rejects_unknown_email(auth_client, fake_auth, persona):
    client, _ = auth_client
    email = f"missing.{persona}@example.com"
    r = client.post(f"/auth/{persona}/email-otp/request", json={"email": email, "login_only": True})
    assert r.status_code == 404
    assert r.json()["detail"] == "User does not exist"
    assert fake_auth.otp_sent_to == []

    client.post(f"/auth/{persona}/email-otp/request", json={"email": email})
    r = client.post(f"/auth/{persona}/email-otp/verify", json={"email": email, "code": "123456", "login_only": True})
    assert r.status_code == 404
    assert r.json()["detail"] == "User does not exist"


# --- access token verification ------------------------------------------------------------------


def _linked_token(Session, fake_auth, **mint_kw):
    _uid, auth_uid = _add_user(Session, fake_auth, username="tokuser", role=UserRole.ta)
    return mint_token(auth_uid, **mint_kw)


def test_valid_hs256_token_accepted(auth_client, fake_auth):
    client, Session = auth_client
    assert _me(client, _linked_token(Session, fake_auth)).status_code == 200


@pytest.mark.parametrize(
    "mint_kw",
    [
        {"expires_in": -10},
        {"audience": "anon"},
        {"secret": "a-different-secret-that-is-at-least-32-chars"},
    ],
    ids=["expired", "wrong-audience", "wrong-secret"],
)
def test_bad_tokens_rejected(auth_client, fake_auth, mint_kw):
    client, Session = auth_client
    assert _me(client, _linked_token(Session, fake_auth, **mint_kw)).status_code == 401


def test_token_for_unknown_auth_user_rejected(auth_client, fake_auth):
    client, _ = auth_client
    assert _me(client, mint_token("11111111-1111-1111-1111-111111111111")).status_code == 401


def test_es256_token_verified_via_jwks(auth_client, fake_auth, monkeypatch):
    """Hosted projects (and the current local CLI) sign with asymmetric keys published as JWKS."""
    client, Session = auth_client
    _uid, auth_uid = _add_user(Session, fake_auth, username="es", role=UserRole.ta)

    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()  # noqa: E731
    jwk = {"kty": "EC", "crv": "P-256", "x": b64(nums.x), "y": b64(nums.y), "kid": "k1", "alg": "ES256", "use": "sig"}

    async def fake_fetch(force: bool = False):
        return [jwk]

    monkeypatch.setattr(supabase_jwt, "_fetch_jwks", fake_fetch)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    claims = jose_jwt.get_unverified_claims(mint_token(auth_uid))
    good = jose_jwt.encode(claims, pem.decode(), algorithm="ES256", headers={"kid": "k1"})
    assert _me(client, good).status_code == 200

    unknown_kid = jose_jwt.encode(claims, pem.decode(), algorithm="ES256", headers={"kid": "nope"})
    assert _me(client, unknown_kid).status_code == 401

    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    forged = jose_jwt.encode(claims, other.decode(), algorithm="ES256", headers={"kid": "k1"})
    assert _me(client, forged).status_code == 401
