"""Read-only project statistics cached with the framework Redis backend."""

from __future__ import annotations

import datetime as dt
from typing import Literal, TypedDict, cast

from sqlalchemy import func, select

from apps.examples.models import ExampleProject
from oldman.cache import RedisCache
from oldman.db import db_manager

CACHE_TTL = 30
CACHE_KEY = "counts"
# This namespace owns only the Demo statistic, never Session or other cache keys.
project_cache = RedisCache(
    "CACHE",
    namespace="oldman_epg_dashboard:examples:project-statistics",
    serializer="json",
    timeout=5,
)


class ProjectStatistics(TypedDict):
    """JSON-compatible snapshot; its source is determined on each read."""

    counts: dict[str, int]
    total: int
    calculated_at: str


async def read_project_statistics(
    *, refresh: bool = False,
) -> tuple[ProjectStatistics, Literal["cache", "database"]]:
    """Use a cached snapshot unless missing or explicitly recalculating it."""
    if not refresh:
        cached = await project_cache.get(CACHE_KEY)
        if cached is not None:
            return cast(ProjectStatistics, cached), "cache"

    statistics = await calculate_project_statistics()
    await project_cache.set(CACHE_KEY, statistics, ttl=CACHE_TTL)
    return statistics, "database"


async def calculate_project_statistics() -> ProjectStatistics:
    """Read one fresh snapshot; cache and process examples share this same query."""
    # Concurrent misses may repeat this small read-only query; no lock is needed.
    async with db_manager.get_read_session() as session:
        rows = await session.execute(
            select(ExampleProject.status, func.count())
            .group_by(ExampleProject.status)
            .order_by(ExampleProject.status)
        )
        counts = {status: count for status, count in rows}
    return ProjectStatistics(
        counts=counts,
        total=sum(counts.values()),
        calculated_at=dt.datetime.now(dt.UTC).isoformat(),
    )


async def clear_project_statistics() -> int:
    """Delete just this shared Demo snapshot; do not clear the Redis database."""
    return await project_cache.delete(CACHE_KEY)
