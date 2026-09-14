"""Exercise dialog mutations against real isolated SQL; browser checks cover intent/guards."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import insert
from sqlalchemy.exc import OperationalError

from oldman.conf.schemas import DatabaseConfig
from oldman.db import DatabaseManager
from apps.examples.models import ExampleProject, ExampleTeam
from tests.test_web_app import create_test_app


class FeedbackWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Check persisted values and genuine rejection, not an echoed success payload."""

    async def test_dialog_operations_save_or_reject_without_partial_changes(self) -> None:
        app = create_test_app()
        from apps.examples.views import messages

        handler = inspect.unwrap(messages.example_feedback_project)
        with tempfile.TemporaryDirectory(prefix="oldman-feedback-test-", dir="/tmp") as temporary:
            database = DatabaseManager(DatabaseConfig(url=f"sqlite+aiosqlite:///{Path(temporary) / 'test.db'}"))
            try:
                await database.create_db_and_tables()
                async with database.get_session() as session:
                    await session.execute(insert(ExampleTeam).values(id=1, name="Team", slug="team", region="test"))
                    await session.execute(insert(ExampleProject).values(
                        id=1, team_id=1, name="Original", slug="original", status="active",
                    ))

                async def submit(**data):
                    """Keep route parsing/rendering real; replace only its owned database."""
                    response = await handler(SimpleNamespace(form=data, app=app))
                    self.assertEqual(response.status, 200)
                    return json.loads(response.body)

                with patch.object(messages, "db_manager", database):
                    reviewed = await submit(project_id="1", operation="review")
                    self.assertEqual(reviewed["data"]["status"], "review")
                    for data in (
                        {"project_id": "1", "operation": "review"},
                        {"project_id": "missing", "operation": "review"},
                        {"project_id": "999", "operation": "review"},
                        {"project_id": "1", "operation": "delete"},
                        {"project_id": "1", "operation": "rename", "name": " "},
                        {"project_id": "1", "operation": "rename", "name": "a" * 151},
                    ):
                        rejected = await submit(**data)
                        self.assertNotEqual(rejected["error_code"], 0)
                        self.assertEqual(rejected["actions"], [])
                    async with database.get_read_session() as session:
                        project = await session.get(ExampleProject, 1)
                        assert project is not None
                        self.assertEqual((project.name, project.slug, project.status), ("Original", "original", "review"))

                    renamed = await submit(project_id="1", operation="rename", name="  <b>Renamed</b>  ")
                    self.assertEqual(renamed["data"]["name"], "<b>Renamed</b>")
                    self.assertIn("&lt;b&gt;Renamed&lt;/b&gt;", renamed["actions"][0]["html"])
                    async with database.get_read_session() as session:
                        project = await session.get(ExampleProject, 1)
                        assert project is not None
                        self.assertEqual((project.name, project.slug), ("<b>Renamed</b>", "original"))
                    async with database.engine.begin() as connection:
                        await connection.run_sync(ExampleProject.__table__.drop)
                    with self.assertRaises(OperationalError):
                        await submit(project_id="1", operation="review")
            finally:
                await database.close()
