"""Authentication via Supabase Auth.

The backend fronts Supabase Auth so it can enforce app rules before a session is issued:
- username + password  -> Supabase password grant (username resolved to the linked auth email)
- worker/client email OTP -> Supabase email OTP (codes are generated, emailed and verified by Supabase)
- refresh / logout     -> Supabase token endpoints

Every response carries the Supabase session; API calls then send the Supabase access token as a Bearer token.
"""

import asyncio
import logging
import re
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import security
from app.auth.supabase_auth import AuthServiceError, AuthSession, SupabaseAuthClient, get_auth_client
from app.config import get_settings
from app.db.session import get_db
from app.models import ClientOrganization, ClientProfile, User, UserRole, WorkerProfile, WorkerStatus
from app.schemas.auth import (
    ClientEmailOtpRequest,
    ClientEmailOtpVerify,
    LoginRequest,
    OtpRequestResponse,
    RefreshRequest,
    TokenResponse,
    WorkerEmailOtpRequest,
    WorkerEmailOtpVerify,
)
from app.services.auth_accounts import finalize_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

AuthClient = Annotated[SupabaseAuthClient, Depends(get_auth_client)]
Db = Annotated[AsyncSession, Depends(get_db)]

_LOGIN_FAILED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid username or password",
)


def _token_response(session: AuthSession, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_at=session.expires_at,
        expires_in=session.expires_in,
        user_id=user.id,
        role=user.role.value,
        locale=user.locale,
    )


async def _worker_block_reason(db: AsyncSession, user: User) -> str | None:
    if user.role != UserRole.worker:
        return None
    wp = (await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))).scalar_one_or_none()
    if wp and wp.status == WorkerStatus.inactive:
        return "Account is inactive"
    if wp and wp.status == WorkerStatus.blacklisted:
        return "Account is blacklisted"
    return None


def _auth_unavailable(e: AuthServiceError) -> HTTPException:
    if e.status_code == 429:
        return HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.")
    logger.warning("Supabase Auth error status=%s code=%s msg=%s", e.status_code, e.code, e.message)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Authentication service error")


# --- username + password ------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Db, client: AuthClient):
    uname = body.username.strip()
    if not uname:
        raise _LOGIN_FAILED
    user = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not user or not user.is_active or not user.supabase_user_id:
        raise _LOGIN_FAILED
    if await _worker_block_reason(db, user):
        raise _LOGIN_FAILED
    try:
        auth_user = await client.admin_get_user(user.supabase_user_id)
        email = auth_user.get("email")
        if not email:
            raise _LOGIN_FAILED
        session = await client.sign_in_with_password(email, body.password)
    except AuthServiceError as e:
        if e.status_code in (400, 401, 404, 422):
            raise _LOGIN_FAILED from e
        raise _auth_unavailable(e) from e
    if session.user_id != user.supabase_user_id:
        raise _LOGIN_FAILED
    session = await finalize_session(client, session, user)
    return _token_response(session, user)


# --- session lifecycle --------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(body: RefreshRequest, db: Db, client: AuthClient):
    try:
        session = await client.refresh(body.refresh_token)
    except AuthServiceError as e:
        if e.status_code in (400, 401, 403, 404):
            raise HTTPException(status_code=401, detail="Session expired") from e
        raise _auth_unavailable(e) from e
    user = (
        await db.execute(select(User).where(User.supabase_user_id == session.user_id))
    ).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Session expired")
    reason = await _worker_block_reason(db, user)
    if reason:
        raise HTTPException(status_code=401, detail=reason)
    session = await finalize_session(client, session, user)
    return _token_response(session, user)


@router.post("/logout", status_code=204)
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    client: AuthClient,
):
    if credentials:
        try:
            await client.sign_out(credentials.credentials)
        except AuthServiceError:
            pass  # already expired/revoked: nothing to do
    return Response(status_code=204)


# --- email OTP (worker + client) ----------------------------------------------------------------


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _locale_from_request(locale: str | None) -> str:
    if not locale:
        return "en"
    return "zh" if locale.strip().lower().startswith("zh") else "en"


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    return result.scalar_one_or_none()


_CODE_RE = re.compile(r"\b(\d{6})\b")


async def _read_dev_mailbox_code(email: str) -> str | None:
    """Local dev only: fetch the code Supabase just mailed to Mailpit, so the UI can show it."""
    base = get_settings().supabase_dev_mailbox_url.rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            for _ in range(10):
                r = await http.get(f"{base}/api/v1/search", params={"query": f"to:{email}", "limit": 1})
                msgs = (r.json() or {}).get("messages") or []
                if msgs:
                    m = await http.get(f"{base}/api/v1/message/{msgs[0]['ID']}")
                    data = m.json() or {}
                    found = _CODE_RE.search(data.get("Text") or "") or _CODE_RE.search(data.get("HTML") or "")
                    if found:
                        return found.group(1)
                await asyncio.sleep(0.3)
    except (httpx.HTTPError, ValueError, KeyError):
        logger.warning("Could not read dev mailbox at %s", base)
    return None


async def _request_email_otp(db: AsyncSession, client: SupabaseAuthClient, email: str, role: UserRole, login_only: bool):
    norm = _normalize_email(email)
    if not norm:
        raise HTTPException(status_code=400, detail="Invalid email")
    if login_only:
        user = await _find_user_by_email(db, norm)
        if not user or user.role != role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User does not exist")
    settings = get_settings()
    if settings.otp_dev_mode and settings.supabase_dev_mailbox_url:
        # Clear older messages for this address so we never echo a stale code.
        await _delete_dev_mailbox_messages(norm)
    try:
        await client.send_email_otp(norm, create_user=True)
    except AuthServiceError as e:
        raise _auth_unavailable(e) from e
    dev_code = await _read_dev_mailbox_code(norm) if settings.otp_dev_mode else None
    return OtpRequestResponse(dev_code=dev_code, delivery="dev" if dev_code else "email")


async def _delete_dev_mailbox_messages(email: str) -> None:
    base = get_settings().supabase_dev_mailbox_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.delete(f"{base}/api/v1/search", params={"query": f"to:{email}"})
    except httpx.HTTPError:
        pass


async def _verify_email_otp(
    db: AsyncSession,
    client: SupabaseAuthClient,
    email: str,
    code: str,
    *,
    role: UserRole,
    login_only: bool,
    locale: str | None,
) -> TokenResponse:
    norm = _normalize_email(email)
    try:
        session = await client.verify_email_otp(norm, code.strip())
    except AuthServiceError as e:
        if e.status_code in (400, 401, 403, 404, 422):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code") from e
        raise _auth_unavailable(e) from e

    user = (
        await db.execute(select(User).where(User.supabase_user_id == session.user_id))
    ).scalar_one_or_none()
    if user is None:
        user = await _find_user_by_email(db, norm)
        if user is not None and user.supabase_user_id and user.supabase_user_id != session.user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is linked to a different login. Contact support.",
            )

    if user:
        if user.role != role:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already registered with a different account type",
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
        reason = await _worker_block_reason(db, user)
        if reason:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)
    else:
        if login_only:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User does not exist")
        user = await _create_app_user(db, norm, role, _locale_from_request(locale))

    if user.supabase_user_id != session.user_id:
        user.supabase_user_id = session.user_id
        await db.flush()
    session = await finalize_session(client, session, user)
    return _token_response(session, user)


async def _create_app_user(db: AsyncSession, email: str, role: UserRole, locale: str) -> User:
    if role == UserRole.worker:
        user = User(email=email, role=UserRole.worker, locale=locale)
        db.add(user)
        await db.flush()
        db.add(WorkerProfile(user_id=user.id))
    else:
        org = ClientOrganization(name="New client organization")
        db.add(org)
        await db.flush()
        user = User(email=email, role=UserRole.client, locale=locale, client_org_id=org.id)
        db.add(user)
        await db.flush()
        db.add(ClientProfile(user_id=user.id, company_name="", contact_email=email))
    await db.flush()
    return user


@router.post("/worker/email-otp/request", response_model=OtpRequestResponse)
async def worker_email_otp_request(body: WorkerEmailOtpRequest, db: Db, client: AuthClient):
    return await _request_email_otp(db, client, str(body.email), UserRole.worker, body.login_only)


@router.post("/worker/email-otp/verify", response_model=TokenResponse)
async def worker_email_otp_verify(body: WorkerEmailOtpVerify, db: Db, client: AuthClient):
    return await _verify_email_otp(
        db, client, str(body.email), body.code, role=UserRole.worker, login_only=body.login_only, locale=body.locale
    )


@router.post("/client/email-otp/request", response_model=OtpRequestResponse)
async def client_email_otp_request(body: ClientEmailOtpRequest, db: Db, client: AuthClient):
    return await _request_email_otp(db, client, str(body.email), UserRole.client, body.login_only)


@router.post("/client/email-otp/verify", response_model=TokenResponse)
async def client_email_otp_verify(body: ClientEmailOtpVerify, db: Db, client: AuthClient):
    return await _verify_email_otp(
        db, client, str(body.email), body.code, role=UserRole.client, login_only=body.login_only, locale=body.locale
    )
