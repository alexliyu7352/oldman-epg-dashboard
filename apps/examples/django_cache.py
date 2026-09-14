"""Optional interoperability Demo using Django's real built-in Redis backend."""

import asyncio
import importlib
import json
import pickle
from uuid import uuid4

import typer

from oldman.cli import Command
from oldman.compat.django.cache import get_django_cache, set_django_cache
from oldman.conf import settings
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.providers.redis import redis_client

from .cache_example import calculate_project_statistics


class DjangoCacheDemo(Command):
    """Exchange real statistics and edge values without installing a Django website."""

    name = "django-cache"
    help = _("Verify project statistics and cache values against Django's real Redis backend.")

    async def handle(self) -> None:
        """Use only UUID-owned keys; Django stays an optional demonstration dependency."""
        try:
            backend = importlib.import_module("django.core.cache.backends.redis")
        except ModuleNotFoundError as error:
            if error.name == "django":
                raise RuntimeError("This optional Demo requires Django; follow the isolated setup in README.md") from error
            raise
        config = settings.redis[settings.cache.client]
        django = backend.RedisCache(config.redis_url, {"OPTIONS": {"protocol": config.protocol}})
        prefix = f"oldman_epg_dashboard:examples:django:{uuid4().hex}"
        incoming, outgoing = f"{prefix}:incoming", f"{prefix}:outgoing"
        missing = object()
        try:
            snapshot = await calculate_project_statistics()
            values = (snapshot, 0, -2, False, True, None, "中文", b"bytes")
            for value in values:
                await django.aset(incoming, value, timeout=30)
                received = await get_django_cache(incoming, missing)
                assert received == value and type(received) is type(value)
                await set_django_cache(outgoing, value, lock_timeout=30)
                received = await django.aget(outgoing, missing)
                assert received == value and type(received) is type(value)
            connection = await redis_client.using(settings.cache.client).async_get_bin_conn()
            assert 0 < await connection.ttl(f":1:{outgoing}") <= 30
            await set_django_cache(outgoing, None, lock_timeout=None)
            assert await django.aget(outgoing, missing) is None
            assert await connection.ttl(f":1:{outgoing}") == -1
            for timeout in (0, -1):
                await django.aset(outgoing, snapshot, timeout=30)
                await set_django_cache(outgoing, snapshot, lock_timeout=timeout)
                assert await django.aget(outgoing, missing) is missing
            await set_django_cache(outgoing, snapshot, lock_timeout=1)
            await asyncio.sleep(1.1)
            assert await get_django_cache(outgoing, missing) is missing
            await django.aset(outgoing, snapshot, timeout=30, version=2)
            assert await get_django_cache(outgoing, missing) is missing
            assert await django.aget(outgoing, version=2) == snapshot

            await connection.set(f":1:{incoming}", b"not a pickle", ex=30)
            for read in (get_django_cache, django.aget):
                try:
                    await read(incoming)
                except pickle.UnpicklingError:
                    pass
                else:
                    raise AssertionError("Corrupt cache data must not be treated as a miss")
            typer.echo(json.dumps({
                "statistics": snapshot, "bidirectional_values": len(values), "types_preserved": True,
                "ttl_and_expiry": True, "missing_differs_from_none": True,
                "version_2_is_separate": True, "corrupt_data_raises": True,
            }, ensure_ascii=False))
        finally:
            try:
                await django.adelete_many([incoming, outgoing])
                await django.adelete(outgoing, version=2)
            finally:
                # Django 5.2 RedisCache.close is a no-op; release only this backend's native pools.
                for pool in django._cache._pools.values():
                    await asyncio.to_thread(pool.disconnect)
                try:
                    await redis_client.close()
                finally:
                    await db_manager.close()
