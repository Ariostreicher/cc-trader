"""Shared Redis client."""

from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from .config import settings

_pool = ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True, max_connections=50)


def get_redis() -> Redis:
    return Redis(connection_pool=_pool)
