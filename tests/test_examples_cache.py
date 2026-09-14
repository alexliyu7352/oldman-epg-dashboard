"""Exercise the cache example with owned Redis and an isolated SQLite database."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event, insert, update
from sqlalchemy.exc import OperationalError

from apps.examples import cache_example as example
from apps.examples.models import ExampleProject, ExampleTeam
from oldman.cache import RedisCache
from oldman.conf.schemas import DatabaseConfig, RedisConfig
from oldman.db import DatabaseManager
from oldman.providers.redis import RedisClientRegistry

ROOT = Path(__file__).resolve().parents[1]


class CacheExampleTests(unittest.IsolatedAsyncioTestCase):
    """Keep storage, SQL and process boundaries real without a full browser gate."""

    async def test_cache_snapshot_lifecycle_and_failures(self) -> None:
        """Hits avoid SQL, refresh/expiry expose changes and failures propagate."""
        support = runpy.run_path(str(ROOT / "scripts/verify-dashboard-browser-with-server.py"))
        with tempfile.TemporaryDirectory(prefix="oldman-cache-test-") as temporary:
            root = Path(temporary)
            with support["owned_redis_server"](root / "redis", environment=os.environ) as url:
                registry = RedisClientRegistry(RedisConfig.model_validate({"CACHE": {"redis_url": url}}))
                cache = RedisCache(registry.using("CACHE"), namespace=example.project_cache.namespace,
                                   serializer="json", timeout=1)
                database = DatabaseManager(DatabaseConfig(url=f"sqlite+aiosqlite:///{root / 'test.db'}"))
                try:
                    await database.create_db_and_tables()
                    statements: list[str] = []

                    def record_sql(_conn, _cursor, statement, _parameters, _context, _many):
                        """Observe real SQL instead of replacing the query with a mock."""
                        statements.append(statement)

                    event.listen(database.engine.sync_engine, "before_cursor_execute", record_sql)
                    with patch.object(example, "project_cache", cache), patch.object(example, "db_manager", database):
                        empty, source = await example.read_project_statistics()
                        self.assertEqual((empty["counts"], empty["total"], source), ({}, 0, "database"))
                        statements.clear()
                        self.assertEqual(await example.read_project_statistics(), (empty, "cache"))
                        self.assertEqual(statements, [])

                        async with database.get_session() as session:
                            await session.execute(insert(ExampleTeam).values(id=1, name="Test", slug="test", region="test"))
                            await session.execute(insert(ExampleProject), [
                                {"id": 1, "team_id": 1, "name": "One", "slug": "one", "status": "planned"},
                                {"id": 2, "team_id": 1, "name": "Two", "slug": "two", "status": "active"},
                            ])
                        fresh, source = await example.read_project_statistics(refresh=True)
                        self.assertEqual((fresh["counts"], fresh["total"], source), ({"active": 1, "planned": 1}, 2, "database"))
                        connection = await registry.using("CACHE").async_get_bin_conn()
                        key = cache.build_key(example.CACHE_KEY)
                        self.assertGreater(await connection.pttl(key), 28000)

                        # Another Python process reads the same bytes, not a local dictionary.
                        child = await asyncio.create_subprocess_exec(
                            sys.executable, "-c",
                            "import json,sys,redis; print(json.dumps(json.loads(redis.Redis.from_url(sys.argv[1], protocol=2).get(sys.argv[2]))))",
                            url, key, stdout=asyncio.subprocess.PIPE,
                        )
                        output, _ = await child.communicate()
                        self.assertEqual(child.returncode, 0)
                        self.assertEqual(json.loads(output), fresh)

                        await connection.pexpire(key, 1000)
                        statements.clear()
                        self.assertEqual(await example.read_project_statistics(), (fresh, "cache"))
                        self.assertEqual(statements, [])
                        self.assertLessEqual(await connection.pttl(key), 1000)  # A hit must not reset the TTL.
                        async with database.get_session() as session:
                            await session.execute(update(ExampleProject).where(ExampleProject.id == 1).values(status="active"))
                        self.assertEqual((await example.read_project_statistics())[0], fresh)
                        await connection.pexpire(key, 1)
                        await asyncio.sleep(0.02)
                        expired, source = await example.read_project_statistics()
                        self.assertEqual((expired["counts"], source), ({"active": 2}, "database"))

                        await connection.set("unrelated", "keep")
                        self.assertEqual(await example.clear_project_statistics(), 1)
                        self.assertEqual(await example.clear_project_statistics(), 0)
                        self.assertEqual(await connection.get("unrelated"), b"keep")
                        async with database.engine.begin() as conn:
                            await conn.run_sync(ExampleProject.__table__.drop)
                        with self.assertRaises(OperationalError):
                            await example.read_project_statistics()
                        self.assertIsNone(await cache.get(example.CACHE_KEY))
                        await connection.shutdown(nosave=True)
                        with self.assertRaises((RedisConnectionError, TimeoutError)):
                            await example.read_project_statistics()
                finally:
                    await database.close()
                    await registry.close()
