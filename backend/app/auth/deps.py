from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.supabase_jwt import decode_supabase_token
from app.db.session import get_db
from app.models import User, UserRole, WorkerProfile, WorkerStatus

security = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Optional[User]:
    if not credentials:
        return None
    try:
        claims = await decode_supabase_token(credentials.credentials)
        auth_uid = str(claims["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        return None
    result = await db.execute(select(User).where(User.supabase_user_id == auth_uid))
    return result.scalar_one_or_none()


async def get_current_user(
    user: Annotated[Optional[User], Depends(get_current_user_optional)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled")
    if user.role == UserRole.worker:
        result = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
        worker = result.scalar_one_or_none()
        if worker and worker.status in (WorkerStatus.inactive, WorkerStatus.blacklisted):
            detail = "Account is inactive" if worker.status == WorkerStatus.inactive else "Account is blacklisted"
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    return user


def require_roles(*roles: UserRole):
    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _dep
