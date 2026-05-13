"""Symbol search."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from ....models.user import User
from ....schemas.market import SearchResult
from ....services.market.registry import MarketDataService
from ...deps import current_user

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/symbols", response_model=List[SearchResult])
async def search_symbols(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=50),
    _: User = Depends(current_user),
) -> List[SearchResult]:
    items = await MarketDataService.search(q, limit=limit)
    return [SearchResult(**i) for i in items]
