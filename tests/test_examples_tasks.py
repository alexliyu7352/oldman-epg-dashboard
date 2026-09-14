"""Check real task business effects without replacing the Taskiq transport tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import inspect
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import insert

from apps.examples import tasks
from apps.examples.models import ExampleProject, ExampleTask, ExampleTeam
from oldman.conf.schemas import DatabaseConfig, DefaultSettings
from oldman.db import DatabaseManager
from oldman.storage.registry import StorageRegistry


class TaskExampleTests(unittest.IsolatedAsyncioTestCase):
    """Use isolated SQLite and filesystem Storage; native decorated calls remain real."""

    async def test_query_reads_once_and_handles_real_expiry(self) -> None:
        """Only rendering/ownership plumbing is isolated; the backend uses real Redis."""
        from redis.asyncio import Redis
        from taskiq import TaskiqResult
        from taskiq.serializers import ORJSONSerializer
        from taskiq_redis import RedisAsyncResultBackend
        from tests.test_web_app import create_test_app

        create_test_app()
        from apps.examples.views import tasks as views
        from oldman.tasks.distributed import broker

        support = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/verify-dashboard-browser-with-server.py"))
        with tempfile.TemporaryDirectory(prefix="oldman-task-query-") as directory:
            with support["owned_redis_server"](Path(directory) / "redis", environment=os.environ) as url:
                backend = RedisAsyncResultBackend(url, keep_results=True, result_ex_time=30, serializer=ORJSONSerializer())
                client = Redis.from_url(url)
                task_id = "a" * 32
                owned = {"task_id": task_id, "ignored": False}
                await client.set(f"owner:task:{task_id}", json.dumps(owned))
                render = AsyncMock(side_effect=lambda request, **context: context)
                try:
                    with (
                        patch.object(views, "_client", AsyncMock(return_value=client)),
                        patch.object(views, "_owner_prefix", return_value="owner"),
                        patch.object(views, "_result", render),
                        patch.object(broker, "result_backend", backend),
                        patch.object(backend, "is_result_ready", wraps=backend.is_result_ready) as readiness,
                    ):
                        # Sanic's route annotation narrows this to a coroutine;
                        # at runtime it preserves the decorated callable.
                        handler = inspect.unwrap(cast(Callable[..., Awaitable[Any]], views.example_task_result))
                        request = SimpleNamespace()
                        self.assertEqual((await handler(request, task_id))["outcome"], "unavailable")
                        await backend.set_result(task_id, TaskiqResult(is_err=False, return_value={"real": True}, execution_time=0))
                        self.assertEqual((await handler(request, task_id))["value"], {"real": True})
                        await client.pexpire(backend._task_name(task_id), 1)
                        await asyncio.sleep(0.02)
                        self.assertEqual((await handler(request, task_id))["outcome"], "unavailable")
                        readiness.assert_not_called()
                finally:
                    await backend.shutdown()
                    await client.aclose()

    async def test_summary_export_and_conditional_completion(self) -> None:
        """Database values reach exports; duplicate completion changes the row once."""
        with tempfile.TemporaryDirectory(prefix="oldman-task-example-") as directory:
            root = Path(directory)
            database = DatabaseManager(DatabaseConfig(url=f"sqlite+aiosqlite:///{root / 'test.db'}"))
            config = DefaultSettings.model_validate({"storages": {"default": {
                "backend": "oldman.storage.backends.filesystem.FileSystemStorage",
                "options": {"location": str(root / "media")},
            }}})
            storage = StorageRegistry(lambda: config)
            storage.init_app()
            try:
                await database.create_db_and_tables()
                async with database.get_session() as session:
                    await session.execute(insert(ExampleTeam).values(id=1, name="Team", slug="team", region="local"))
                    await session.execute(insert(ExampleProject).values(id=1, team_id=1, name="真实项目", slug="project", budget="12.50"))
                    await session.execute(insert(ExampleTask).values(id=1, project_id=1, title="Complete once"))
                with patch.object(tasks, "db_manager", database), patch.object(tasks, "storages", storage):
                    summary = await tasks.project_summary(1)
                    self.assertEqual((summary["name"], summary["budget"], summary["task_count"]), ("真实项目", "12.50", 1))
                    exported = await tasks.export_project(1, 7, "sample")
                    self.assertEqual(exported["file"], "task-exports/7/sample.json")
                    saved = json.loads((root / "media" / str(exported["file"])).read_text())
                    self.assertEqual((saved["name"], saved["task_count"]), ("真实项目", 1))
                    first = await tasks.complete_example_task(1)
                    second = await tasks.complete_example_task(1)
                    self.assertEqual((first["changed"], second["changed"]), (True, False))
                    with self.assertRaisesRegex(ValueError, "Intentional Demo failure"):
                        await tasks.project_summary(1, fail=True)
                    with self.assertRaisesRegex(ValueError, "no longer exists"):
                        await tasks.complete_example_task(999)
                async with database.get_read_session() as session:
                    record = await session.get(ExampleTask, 1)
                    self.assertIsNotNone(record)
                    assert record is not None
                    self.assertEqual((record.status, record.is_completed), ("done", True))
            finally:
                await database.close()
