"""Auth router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.user import User
from ....schemas.auth import (
    LoginIn,
    PasswordResetIn,
    PasswordResetRequestIn,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UserOut,
    VerifyIn,
)
from ....services import auth as svc
from ...deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)) -> UserOut:
    user = await svc.register_user(
        db, email=payload.email, password=payload.password, full_name=payload.full_name
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginIn, request: Request, db: AsyncSession = Depends(get_db)) -> TokenPair:
    user = await svc.authenticate(db, email=payload.email, password=payload.password)
    access, refresh, expires_in = await svc.issue_token_pair(
        db,
        user=user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshIn, request: Request, db: AsyncSession = Depends(get_db)
) -> TokenPair:
    access, new_refresh, expires_in = await svc.rotate_refresh(
        db,
        refresh_token=payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return TokenPair(access_token=access, refresh_token=new_refresh, expires_in=expires_in)


@router.post("/logout", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshIn, db: AsyncSession = Depends(get_db)) -> None:
    await svc.logout(db, refresh_token=payload.refresh_token)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: PasswordResetRequestIn, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    # Returns the same response whether or not the email exists, to avoid
    # account-enumeration. A real deployment emails the token here.
    await svc.request_password_reset(db, email=payload.email)
    return {"ok": True}


@router.post("/password/reset", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(payload: PasswordResetIn, db: AsyncSession = Depends(get_db)) -> None:
    await svc.reset_password(db, token=payload.token, new_password=payload.new_password)


@router.post("/verify", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def verify(payload: VerifyIn, db: AsyncSession = Depends(get_db)) -> None:
    await svc.verify_email(db, token=payload.token)


@router.get("/me", response_model=UserOut)
async def me(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UserOut:
    # Query subscription explicitly to avoid lazy-load in async context
    # (raises MissingGreenlet otherwise).
    from sqlalchemy import select as _select
    from ....models.user import Subscription as _Subscription

    out = UserOut.model_validate(user)
    sub_res = await db.execute(
        _select(_Subscription).where(_Subscription.user_id == user.id)
    )
    sub = sub_res.scalar_one_or_none()
    if sub:
        out.tier = sub.tier
    return out
