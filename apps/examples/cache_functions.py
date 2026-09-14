"""Use the existing result decorator around the real project statistics query."""

import asyncio
import json
from uuid import uuid4

import typer

from oldman.cache import cache_async_response, redis_cache
from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.providers.redis import redis_client

from .cache_example import ProjectStatistics, calculate_project_statistics


class CachedStats(Command):
    """Observe one finite function-result cache without touching another run's keys."""

    name = "cached-stats"
    help = _("Observe real project statistics reused by the async function cache decorator.")

    async def handle(self) -> None:
        """The decorated query remains real SQL; a cache hit reuses its original timestamp."""
        prefix = f"oldman_epg_dashboard:examples:function:{uuid4().hex}"

        @cache_async_response(timeout=1, prefix=prefix)
        async def statistics() -> ProjectStatistics:
            """Recompute only on a miss or when the existing decorator falls back."""
            return await calculate_project_statistics()

        try:
            first = await statistics()
            second = await statistics()
            assert second == first
            await asyncio.sleep(1.1)
            expired = await statistics()
            assert expired["calculated_at"] != first["calculated_at"]
            typer.echo(json.dumps({"first": first, "cache_hit": second, "after_expiry": expired}, ensure_ascii=False))
        finally:
            try:
                await redis_cache.delete_match(f"{prefix}*")
            finally:
                try:
                    await redis_client.close()
                finally:
                    await db_manager.close()
