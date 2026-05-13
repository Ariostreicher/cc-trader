"""Auth service: registration, login, refresh rotation, password reset."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.security import (
    create_token,
    decode_token,
    generate_reset_token,
    hash_password,
    verify_password,
)
from ..models.user import RefreshToken, Subscription, SubscriptionTier, User, UserRole


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    res = await db.execute(select(User).where(User.email == email.lower()))
    return res.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    res = await db.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


async def register_user(
    db: AsyncSession, *, email: str, password: str, full_name: Optional[str]
) -> User:
    if await get_user_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(
        email=email.lower(),
        hashed_password=hash_password(password),
        full_name=full_name,
        role=UserRole.user,
        is_active=True,
        is_verified=False,
        verification_token=generate_reset_token(),
    )
    db.add(user)
    await db.flush()
    # default free subscription
    db.add(Subscription(user_id=user.id, tier=SubscriptionTier.free))
    await db.flush()
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    return user


async def issue_token_pair(
    db: AsyncSession,
    *,
    user: User,
    family: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[str, str, int]:
    access, _access_exp, _ = create_token(subject=user.id, kind="access")
    refresh, refresh_exp, family_id = create_token(subject=user.id, kind="refresh", family=family)
    db.add(
        RefreshToken(
            user_id=user.id,
            family=family_id,
            token_hash=_hash_token(refresh),
            expires_at=refresh_exp,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    await db.flush()
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    return access, refresh, expires_in


async def rotate_refresh(
    db: AsyncSession,
    *,
    refresh_token: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[str, str, int]:
    try:
        payload = decode_token(refresh_token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    if payload.get("kind") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token kind")

    token_hash = _hash_token(refresh_token)
    res = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    record = res.scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token unknown")
    if record.revoked_at is not None:
        # Reuse of revoked token → revoke whole family.
        await _revoke_family(db, family=record.family)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh reuse detected")
    if record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh expired")

    user = await get_user_by_id(db, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user inactive")

    record.revoked_at = datetime.now(timezone.utc)

    access, new_refresh, expires_in = await issue_token_pair(
        db, user=user, family=record.family, user_agent=user_agent, ip_address=ip_address
    )
    return access, new_refresh, expires_in


async def _revoke_family(db: AsyncSession, *, family: str) -> None:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        select(RefreshToken).where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
    )
    for tok in res.scalars():
        tok.revoked_at = now


async def logout(db: AsyncSession, *, refresh_token: str) -> None:
    token_hash = _hash_token(refresh_token)
    res = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    record = res.scalar_one_or_none()
    if record:
        record.revoked_at = datetime.now(timezone.utc)


async def request_password_reset(db: AsyncSession, *, email: str) -> Optional[str]:
    user = await get_user_by_email(db, email)
    if not user:
        return None  # do not leak existence
    user.reset_token = generate_reset_token()
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    return user.reset_token


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> None:
    res = await db.execute(select(User).where(User.reset_token == token))
    user = res.scalar_one_or_none()
    if not user or not user.reset_token_expires:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid reset token")
    if user.reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reset token expired")
    user.hashed_password = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None


async def verify_email(db: AsyncSession, *, token: str) -> None:
    res = await db.execute(select(User).where(User.verification_token == token))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid verification token")
    user.is_verified = True
    user.verification_token = None
