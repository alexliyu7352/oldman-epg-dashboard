"""Walk the model cache example with an owned Redis and an isolated SQLite database."""

from __future__ import annotations

import asyncio
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oldman.conf as conf
from oldman.conf.schemas import DatabaseConfig, DefaultSettings, RedisConfig
from oldman.db import DatabaseManager
from oldman.providers.redis import RedisClientRegistry

from apps.examples import model_cache as example
from apps.examples import tasks
from apps.examples.models import ExampleProject, ExampleTask, ExampleTeam

ROOT = Path(__file__).resolve().parents[1]


async def settled_read(project_id: int) -> example.TaskRead:
    """Read once this process's own invalidation has landed; until then a read bypasses the cache."""
    for _ in range(200):
        read = await example.read_project_tasks(project_id)
        if read.source != "bypassed":
            return read
        await asyncio.sleep(0.01)
    raise AssertionError("the invalidation after a commit did not land")


def titles(read: example.TaskRead) -> list[str]:
    return [task.title for task in read.tasks]


class ModelCacheExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_write_invalidates_what_the_page_says(self) -> None:
        support = runpy.run_path(str(ROOT / "scripts/verify-dashboard-browser-with-server.py"))
        with tempfile.TemporaryDirectory(prefix="oldman-model-cache-test-") as temporary:
            root = Path(temporary)
            with support["owned_redis_server"](root / "redis", environment=os.environ) as url:
                settings = DefaultSettings()
                settings.core.namespace = "model_cache_example_test"
                registry = RedisClientRegistry(RedisConfig.model_validate({"CACHE": {"redis_url": url}}))
                database = DatabaseManager(DatabaseConfig(url=f"sqlite+aiosqlite:///{root / 'test.db'}"))
                try:
                    with (
                        patch.dict(conf.__dict__, {"settings": settings}),
                        patch("oldman.db.sqlalchemy.cache.redis_client", registry),
                        patch("oldman.db.session.db_manager", database),
                        patch.object(example, "db_manager", database),
                        patch.object(tasks, "db_manager", database),
                    ):
                        await database.create_db_and_tables()
                        async with database.get_session() as session:
                            session.add(ExampleTeam(id=1, name="Team", slug="team", region="local"))
                            session.add_all([
                                ExampleProject(id=1, team_id=1, name="One", slug="one"),
                                ExampleProject(id=2, team_id=1, name="Two", slug="two"),
                            ])
                            await session.flush()
                            session.add_all([
                                ExampleTask(id=1, project_id=1, title="Draft", position=0),
                                ExampleTask(id=2, project_id=1, title="Review", position=1),
                                ExampleTask(id=3, project_id=2, title="Ship", position=0),
                            ])
                        self.assertEqual([(1, "One"), (2, "Two")], await example.projects_with_tasks())

                        first = await settled_read(1)
                        self.assertEqual(("misses", ["Draft", "Review"]), (first.source, titles(first)))
                        self.assertEqual("hits", (await settled_read(1)).source)

                        written = await example.rename_first_task(1)
                        assert written is not None
                        self.assertEqual(("Draft", "Draft (ORM)"), (written.before, written.after))
                        renamed = await settled_read(1)
                        self.assertEqual(("misses", ["Draft (ORM)", "Review"]), (renamed.source, titles(renamed)))

                        other = await example.rename_task_in_another_project(1)
                        assert other is not None
                        self.assertEqual((2, "Ship (ORM)"), (other.project_id, other.after))
                        self.assertEqual("hits", (await settled_read(1)).source, "another project's write leaves this partition cached")

                        await example.rename_first_task_with_sql(1)
                        stale = await settled_read(1)
                        self.assertEqual(("hits", "Draft (ORM)"), (stale.source, stale.tasks[0].title), "an UPDATE statement is invisible to the cache")
                        await example.invalidate_tasks()
                        fresh = await settled_read(1)
                        self.assertEqual(("misses", "Draft (SQL)"), (fresh.source, fresh.tasks[0].title))

                        project = await example.rename_project(2)
                        assert project is not None
                        self.assertEqual("misses", (await settled_read(1)).source, "invalidate_on: a project write invalidates every task query")

                        await tasks.complete_example_task(2)
                        completed = await settled_read(1)
                        self.assertEqual(("misses", "done"), (completed.source, completed.tasks[1].status), "the worker's bulk UPDATE declares itself")

                        self.assertEqual(3, await example.restore())
                        restored = await settled_read(1)
                        self.assertEqual(["Draft", "Review"], titles(restored))
                        self.assertEqual(["Ship"], titles(await settled_read(2)))
                        stats = restored.stats
                        self.assertGreater(stats.hits, 0)
                        self.assertEqual(0, stats.fallbacks)
                finally:
                    await database.close()
                    await registry.close()


if __name__ == "__main__":
    unittest.main()
