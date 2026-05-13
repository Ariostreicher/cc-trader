"""FastAPI dependencies — current_user, current_admin, db session.

When ``settings.DEV_NO_AUTH`` is true, ALL requests are silently authenticated
as a single auto-created demo user. This lets a single operator run the stack
on their own machine without dealing with registration/login. Never enable
this on a public deployment.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.security import decode_token
from ..db.session import get_db
from ..models.user import Subscription, SubscriptionTier, User, UserRole
from ..services.auth import get_user_by_id

# Sentinel value stored in hashed_password for the demo user.
# Intentionally NOT a valid bcrypt hash so password login can never match it.
# We bypass bcrypt entirely to avoid passlib + bcrypt version conflicts.
_DEMO_PASSWORD_SENTINEL = "__no_auth_mode__"

bearer_scheme = HTTPBearer(auto_error=False)


async def _get_or_create_demo_user(db: AsyncSession) -> User:
    """Returns the singleton demo user, creating it (and a free subscription)
    on first access."""
    email = settings.DEV_USER_EMAIL.lower()
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        email=email,
        # Sentinel — bcrypt is never invoked in DEV_NO_AUTH mode.
        hashed_password=_DEMO_PASSWORD_SENTINEL,
        full_name="Demo Operator",
        role=UserRole.admin,  # admin so the /admin endpoints work too
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(Subscription(user_id=user.id, tier=SubscriptionTier.pro))
    await db.flush()
    return user


async def current_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    if settings.DEV_NO_AUTH:
        return await _get_or_create_demo_user(db)

    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if payload.get("kind") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token kind")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed sub") from exc
    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    return user


async def current_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
