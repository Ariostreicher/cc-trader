"""Watchlist CRUD + asset management."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ....db.session import get_db
from ....models.user import User
from ....models.watchlist import Watchlist, WatchlistAsset
from ....schemas.watchlist import WatchlistAssetIn, WatchlistAssetOut, WatchlistIn, WatchlistOut
from ...deps import current_user

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _can_modify(user: User, wl: Watchlist) -> bool:
    return wl.user_id is not None and wl.user_id == user.id


@router.get("", response_model=List[WatchlistOut])
async def list_watchlists(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> List[WatchlistOut]:
    res = await db.execute(
        select(Watchlist)
        .where((Watchlist.user_id == user.id) | (Watchlist.is_public.is_(True)))
        .options(selectinload(Watchlist.assets))
        .order_by(Watchlist.pinned.desc(), Watchlist.created_at.desc())
    )
    return [WatchlistOut.model_validate(w) for w in res.scalars()]


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    payload: WatchlistIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistOut:
    wl = Watchlist(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        pinned=payload.pinned,
        is_public=False,
    )
    db.add(wl)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "watchlist name already exists")
    # Construct response manually — a freshly created watchlist has no assets,
    # and pydantic model_validate triggers a lazy load on the relationship which
    # explodes in async context (greenlet_spawn missing).
    return WatchlistOut(
        id=wl.id,
        name=wl.name,
        description=wl.description,
        is_public=wl.is_public,
        pinned=wl.pinned,
        created_at=wl.created_at,
        assets=[],
    )


@router.get("/{watchlist_id}", response_model=WatchlistOut)
async def get_watchlist(
    watchlist_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistOut:
    res = await db.execute(
        select(Watchlist)
        .where(Watchlist.id == watchlist_id)
        .options(selectinload(Watchlist.assets))
    )
    wl = res.scalar_one_or_none()
    if not wl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    if not wl.is_public and wl.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed")
    return WatchlistOut.model_validate(wl)


@router.delete("/{watchlist_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(
    watchlist_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id))
    wl = res.scalar_one_or_none()
    if not wl or not _can_modify(user, wl):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    await db.delete(wl)


@router.post(
    "/{watchlist_id}/assets",
    response_model=WatchlistAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_asset(
    watchlist_id: uuid.UUID,
    payload: WatchlistAssetIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistAssetOut:
    res = await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id))
    wl = res.scalar_one_or_none()
    if not wl or not _can_modify(user, wl):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")

    pos_res = await db.execute(
        select(WatchlistAsset).where(WatchlistAsset.watchlist_id == wl.id)
    )
    position = sum(1 for _ in pos_res.scalars())

    asset = WatchlistAsset(
        watchlist_id=wl.id,
        symbol=payload.symbol.upper(),
        exchange=payload.exchange,
        asset_class=payload.asset_class,
        sector=payload.sector,
        note=payload.note,
        position=position,
    )
    db.add(asset)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "symbol already in watchlist")
    return WatchlistAssetOut.model_validate(asset)


@router.delete(
    "/{watchlist_id}/assets/{asset_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT
)
async def remove_asset(
    watchlist_id: uuid.UUID,
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    res = await db.execute(
        select(WatchlistAsset).join(Watchlist).where(
            WatchlistAsset.id == asset_id,
            WatchlistAsset.watchlist_id == watchlist_id,
            Watchlist.user_id == user.id,
        )
    )
    asset = res.scalar_one_or_none()
    if not asset:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    await db.delete(asset)
