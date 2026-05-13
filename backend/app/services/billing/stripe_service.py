"""Stripe billing — checkout sessions + webhook handling.

All Stripe calls are wrapped in ``asyncio.to_thread`` so the sync Stripe SDK
plays nicely with FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...models.audit import BillingRecord
from ...models.user import Subscription, SubscriptionTier, User

logger = logging.getLogger(__name__)

PRICE_TO_TIER = {
    settings.STRIPE_PRICE_PRO: SubscriptionTier.pro,
    settings.STRIPE_PRICE_ENTERPRISE: SubscriptionTier.enterprise,
}


def _client():
    if not settings.STRIPE_API_KEY:
        raise RuntimeError("STRIPE_API_KEY not configured")
    import stripe

    stripe.api_key = settings.STRIPE_API_KEY
    return stripe


async def create_checkout_session(
    *, user: User, price_id: str, success_url: str, cancel_url: str
) -> str:
    """Return the checkout URL the frontend should redirect to."""
    stripe = _client()

    def _create():
        kwargs = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(user.id),
        }
        if user.stripe_customer_id:
            kwargs["customer"] = user.stripe_customer_id
        else:
            kwargs["customer_email"] = user.email
        return stripe.checkout.Session.create(**kwargs)

    session = await asyncio.to_thread(_create)
    return session.url


async def create_portal_session(*, user: User, return_url: str) -> str:
    if not user.stripe_customer_id:
        raise RuntimeError("user has no Stripe customer id yet")
    stripe = _client()
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=user.stripe_customer_id,
        return_url=return_url,
    )
    return session.url


async def handle_webhook(db: AsyncSession, *, payload_bytes: bytes, signature: str) -> None:
    stripe = _client()

    def _verify():
        return stripe.Webhook.construct_event(
            payload_bytes, signature, settings.STRIPE_WEBHOOK_SECRET
        )

    event = await asyncio.to_thread(_verify)

    # Idempotency.
    existing = await db.execute(
        select(BillingRecord).where(BillingRecord.stripe_event_id == event["id"])
    )
    if existing.scalar_one_or_none():
        return

    user_id = _extract_user_id(event)
    if user_id is None:
        logger.warning("billing webhook %s: no user_id resolved", event["type"])
        return

    db.add(
        BillingRecord(
            user_id=user_id,
            stripe_event_id=event["id"],
            event_type=event["type"],
            amount_cents=_extract_amount(event),
            currency=_extract_currency(event),
            payload=event.get("data", {}).get("object", {}),
        )
    )

    typ = event["type"]
    obj = event["data"]["object"]

    if typ == "checkout.session.completed":
        await _apply_subscription(db, user_id=user_id, stripe_object=obj)
    elif typ in {"customer.subscription.updated", "customer.subscription.created"}:
        await _apply_subscription(db, user_id=user_id, stripe_object=obj)
    elif typ == "customer.subscription.deleted":
        await _downgrade_to_free(db, user_id=user_id)


def _extract_user_id(event: dict) -> Optional[uuid.UUID]:
    obj = event["data"]["object"]
    ref = obj.get("client_reference_id") or obj.get("metadata", {}).get("user_id")
    if not ref:
        return None
    try:
        return uuid.UUID(ref)
    except ValueError:
        return None


def _extract_amount(event: dict) -> Optional[int]:
    obj = event["data"]["object"]
    return obj.get("amount_total") or obj.get("amount_paid")


def _extract_currency(event: dict) -> Optional[str]:
    obj = event["data"]["object"]
    return obj.get("currency")


async def _apply_subscription(db: AsyncSession, *, user_id: uuid.UUID, stripe_object: dict) -> None:
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        return

    if stripe_object.get("customer"):
        user.stripe_customer_id = stripe_object["customer"]

    sub_res = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    sub = sub_res.scalar_one_or_none() or Subscription(user_id=user_id)
    if sub.id is None:
        db.add(sub)

    sub.stripe_subscription_id = stripe_object.get("id") or sub.stripe_subscription_id
    price_id = (
        stripe_object.get("plan", {}).get("id")
        or stripe_object.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
    )
    if price_id and price_id in PRICE_TO_TIER:
        sub.tier = PRICE_TO_TIER[price_id]
    if "current_period_end" in stripe_object:
        from datetime import datetime, timezone

        sub.current_period_end = datetime.fromtimestamp(
            stripe_object["current_period_end"], tz=timezone.utc
        )
    sub.cancel_at_period_end = bool(stripe_object.get("cancel_at_period_end"))


async def _downgrade_to_free(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    res = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    sub = res.scalar_one_or_none()
    if sub:
        sub.tier = SubscriptionTier.free
        sub.cancel_at_period_end = False
