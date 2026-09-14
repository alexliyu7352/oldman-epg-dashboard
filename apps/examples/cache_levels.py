"""Observe real cache scope and expiry using read-only project statistics."""

import asyncio
import json
import os
import sys
from uuid import uuid4

import typer

from oldman.cache import MemoryCache, RedisCache, TwoLevelCache
from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.processes import run_subprocess_exec
from oldman.providers.redis import redis_client


async def _peer(namespace: str) -> None:
    """Recompute in another process; no parent memory or live connections are shared."""
    # The module also runs as a child entry, so model imports must follow bootstrap.
    from .cache_example import calculate_project_statistics

    try:
        async with MemoryCache() as memory:
            assert await memory.get("counts") is None
            shared = RedisCache("CACHE", namespace=namespace, serializer="json")
            await TwoLevelCache(memory, shared).set("counts", await calculate_project_statistics(), ttl=30)
            typer.echo(json.dumps({"cache_peer_pid": os.getpid(), "new_process_memory_empty": True}))
    finally:
        try:
            await redis_client.close()
        finally:
            await db_manager.close()


class CacheLevels(Command):
    """Compare process-local snapshots against real cross-process Redis changes."""

    name = "cache-levels"
    help = _("Observe project statistics in memory and two-level caches, including stale local copies.")

    async def handle(self) -> None:
        """Own all local data and a unique Redis namespace; never alter project records."""
        from .cache_example import calculate_project_statistics

        shared = RedisCache("CACHE", namespace=f"oldman_epg_dashboard:examples:levels:{uuid4().hex}",
                            serializer="json")
        wrote_shared = False
        try:
            async with MemoryCache() as memory:
                first = await calculate_project_statistics()
                await memory.set("counts", first, ttl=.15)
                memory_hit = await memory.get("counts") == first
                await asyncio.sleep(.2)
                memory_expired = await memory.get("counts") is None
                assert memory_hit and memory_expired

                levels = TwoLevelCache(memory, shared)
                await levels.set("counts", first, ttl=30)
                wrote_shared = True
                child = await run_subprocess_exec(
                    sys.executable, "-m", "apps.examples.cache_levels", shared.namespace,
                    capture_output=True, check=True, timeout=10,
                )
                typer.echo((child.stdout or b"").decode("utf-8").strip())
                updated = await shared.get("counts")
                assert updated["calculated_at"] != first["calculated_at"]
                local_stale = await levels.get("counts") == first
                assert local_stale
                await memory.delete("counts")
                backfilled = await levels.get("counts") == updated
                assert backfilled

                # Clear only this process's copy, then backfill the remaining Redis TTL.
                await shared.expire("counts", .15)
                await memory.delete("counts")
                assert await levels.get("counts") == updated
                await asyncio.sleep(.2)
                both_expired = await memory.get("counts") is None and await shared.get("counts") is None
                await levels.set("counts", updated, ttl=30)
                await levels.delete("counts")
                both_deleted = await memory.get("counts") is None and await shared.get("counts") is None
                assert both_expired and both_deleted
                typer.echo(json.dumps({
                    "cache_parent_pid": os.getpid(), "memory_hit": memory_hit, "memory_expired": memory_expired,
                    "before": first, "shared_updated": updated, "local_stale": local_stale,
                    "backfilled": backfilled, "both_expired": both_expired, "both_deleted": both_deleted,
                }, ensure_ascii=False))
        finally:
            try:
                if wrote_shared:
                    await shared.clear()
            finally:
                try:
                    await redis_client.close()
                finally:
                    await db_manager.close()


if __name__ == "__main__":
    from oldman import bootstrap_service

    bootstrap_service("web")
    asyncio.run(_peer(sys.argv[1]))
