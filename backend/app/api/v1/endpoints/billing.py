"""Billing endpoints — checkout, portal, webhook."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.user import User
from ....services.billing.stripe_service import (
    create_checkout_session,
    create_portal_session,
    handle_webhook,
)
from ...deps import current_user

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str


class PortalIn(BaseModel):
    return_url: str


class UrlOut(BaseModel):
    url: str


@router.post("/checkout", response_model=UrlOut)
async def checkout(
    payload: CheckoutIn,
    user: User = Depends(current_user),
) -> UrlOut:
    try:
        url = await create_checkout_session(
            user=user,
            price_id=payload.price_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return UrlOut(url=url)


@router.post("/portal", response_model=UrlOut)
async def portal(
    payload: PortalIn,
    user: User = Depends(current_user),
) -> UrlOut:
    try:
        url = await create_portal_session(user=user, return_url=payload.return_url)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return UrlOut(url=url)


@router.post("/webhook", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> None:
    body = await request.body()
    try:
        await handle_webhook(db, payload_bytes=body, signature=stripe_signature)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"webhook error: {exc}")
