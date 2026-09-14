"""Real SQL and asyncio checks for the local background command's ownership."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import insert

from apps.examples import background
from apps.examples.models import ExampleProject, ExampleTeam
from oldman.conf.schemas import DatabaseConfig
from oldman.db import DatabaseManager
from oldman.tasks import BackgroundTaskManager


class BackgroundStatsTests(unittest.IsolatedAsyncioTestCase):
    """Do not substitute a mock manager or count as proof of executed work."""

    async def test_samples_failure_and_cancellation_release_owned_tasks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oldman-background-test-", dir="/tmp") as temporary:
            database = DatabaseManager(DatabaseConfig(url=f"sqlite+aiosqlite:///{Path(temporary) / 'db'}"))
            manager = BackgroundTaskManager()
            try:
                await database.create_db_and_tables()
                async with database.get_session() as session:
                    await session.execute(insert(ExampleTeam).values(id=1, name="Team", slug="team", region="test"))
                    await session.execute(insert(ExampleProject).values(id=1, team_id=1, name="Project", slug="project"))
                with patch.object(background, "db_manager", database):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        await background.BackgroundStats().handle(team_id=1)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["samples"], [1, 1])
                    self.assertEqual(result["running"]["status"], "running")
                    self.assertTrue(result["running"]["is_alive"])
                    self.assertEqual(result["stopped"]["status"], "stopped")
                    self.assertFalse(result["stopped"]["is_alive"])
                    self.assertTrue(result["cleanup_completed"])
                    with self.assertRaises(ValueError):
                        await background.BackgroundStats().handle(team_id=999)
                    self.assertFalse(manager.running)
                    self.assertEqual(manager.get_all_status(), {})
                    self.assertIsNotNone(manager.monitor_task)
                    assert manager.monitor_task is not None
                    self.assertTrue(manager.monitor_task.done())
            finally:
                await database.close()
