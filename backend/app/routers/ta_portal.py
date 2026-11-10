from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_roles
from app.auth.supabase_auth import SupabaseAuthClient, get_auth_client
from app.db.session import get_db
from app.services.auth_accounts import sync_auth_email
from app.models import User, UserRole
from app.schemas.ta_profile import TaStaffProfileOut, TaStaffProfileUpdate

router = APIRouter(prefix="/ta", tags=["ta-portal"])


def _out(user: User) -> TaStaffProfileOut:
    return TaStaffProfileOut(
        display_name=user.display_name,
        email=user.email,
        phone=user.phone,
        username=user.username,
    )


@router.get("/me/profile", response_model=TaStaffProfileOut)
async def get_ta_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    await db.refresh(user)
    return _out(user)


@router.put("/me/profile", response_model=TaStaffProfileOut)
async def update_ta_profile(
    body: TaStaffProfileUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
    auth_client: Annotated[SupabaseAuthClient, Depends(get_auth_client)],
):
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    if body.email is not None:
        em = body.email.strip().lower() or None
        if em:
            clash = await db.execute(
                select(User).where(func.lower(User.email) == em, User.id != user.id)
            )
            if clash.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Email already in use")
            if em != (user.email or "").lower():
                await sync_auth_email(auth_client, user, em)
        user.email = em
    await db.flush()
    await db.refresh(user)
    return _out(user)
