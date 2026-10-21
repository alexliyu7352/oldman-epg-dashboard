"""The MCP example (apps/examples/mcp.py): what it offers, its queries, and the endpoint in the real Web service."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from oldman.mcp import ToolError, mcp
from oldman.web.authentication import issue_access_token
from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.examples import mcp as example_mcp
from apps.examples.models import ExampleProject, ExampleTeam
from config.settings import settings
from scripts.demo_auth import DEMO_API_KEY_NAME
from tests.test_web_app import create_test_app

SUPERUSER = SimpleNamespace(id=1, username="root", display_name="Root", is_active=True, is_staff=True, is_superuser=True)
PLAIN_USER = SimpleNamespace(id=2, username="plain", display_name="Plain", is_active=True, is_staff=False, is_superuser=False)
HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "MCP-Protocol-Version": "2025-11-25"}


class CatalogTests(unittest.TestCase):
    """What the example App offers, and which permission each item needs."""

    def test_project_data_needs_view_projects_and_only_example_caller_is_open(self) -> None:
        catalog = mcp.catalog(["apps.examples"])
        permissions = {name: tool.permission for name, tool in catalog.tools.items()}
        self.assertEqual(
            {
                "example_caller": None,
                "find_example_projects": "examples.view_projects",
                "count_example_projects_by_status": "examples.view_projects",
            },
            {name: permission.name if permission is not None else None for name, permission in permissions.items()},
        )
        # A template, not a fixed resource: a fixed one has no uri_template.
        self.assertEqual(["example-project://{slug}"], [getattr(resource.wire, "uri_template", None) for resource in catalog.resources])
        resource_permission = catalog.resources[0].permission
        self.assertEqual("examples.view_projects", resource_permission.name if resource_permission is not None else None)
        self.assertEqual(["review_example_project"], list(catalog.prompts))


class QueryTests(unittest.IsolatedAsyncioTestCase):
    """The tools and the resource against a real, isolated database."""

    async def asyncSetUp(self) -> None:
        teams, projects = cast(Table, ExampleTeam.__table__), cast(Table, ExampleProject.__table__)
        self.engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            for table in (teams, projects):
                await connection.run_sync(table.create)
            await connection.execute(insert(teams), [{"id": 1, "name": "Core", "slug": "core", "region": "eu", "is_active": True}])
            await connection.execute(
                insert(projects),
                [
                    {"id": 11, "team_id": 1, "name": "Alpha launch", "slug": "alpha", "status": "active", "progress": 40},
                    {"id": 12, "team_id": 1, "name": "Beta", "slug": "beta", "status": "planned", "progress": 0},
                    {"id": 13, "team_id": 1, "name": "Gamma", "slug": "alpha-two", "status": "active", "progress": 0},
                ],
            )
        patcher = patch.object(example_mcp, "db_manager", SimpleNamespace(get_read_session=sessions))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_find_matches_name_or_slug_filters_status_and_counts_beyond_the_limit(self) -> None:
        found = await example_mcp.find_example_projects(text="alpha", limit=1)
        self.assertEqual(2, found.total)
        self.assertEqual(["alpha"], [project.slug for project in found.projects])
        self.assertEqual("Core", found.projects[0].team)
        planned = await example_mcp.find_example_projects(status="planned")
        self.assertEqual(["beta"], [project.slug for project in planned.projects])
        with self.assertRaisesRegex(ToolError, "Unknown status 'lost'"):
            await example_mcp.find_example_projects(status="lost")

    async def test_counts_list_every_status_even_without_projects(self) -> None:
        counts = await example_mcp.count_example_projects_by_status()
        self.assertEqual({"active": 2, "completed": 0, "paused": 0, "planned": 1, "review": 0}, counts)

    async def test_the_resource_reads_one_project_or_none(self) -> None:
        project = await example_mcp.example_project("alpha")
        assert project is not None
        self.assertEqual(("Alpha launch", 40, "0.00", None), (project.name, project.progress, project.budget, project.start_date))
        self.assertIsNone(await example_mcp.example_project("missing"))


class ExampleSettingsTests(unittest.TestCase):
    """The tracked example settings keep the endpoint off, so a fresh clone starts without MCP callers."""

    def test_the_endpoint_is_off_in_the_example_settings(self) -> None:
        self.assertFalse(settings.web.mcp.enabled)
        self.assertEqual([], settings.web.mcp.callers)


class EndpointTests(unittest.TestCase):
    """The endpoint in the real WebService once it is on: who sees what.

    A process builds the WebService once (its SSE extension is process-wide), so the class turns MCP on
    for the one app it builds. The other modules build it with MCP off, as the example settings have it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        for patcher in (patch.object(settings.web.mcp, "enabled", True), patch.object(settings.web.mcp, "callers", [DEMO_API_KEY_NAME])):
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        cls.app = create_test_app()

    def post(self, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        _request, response = self.app.test_client.post("/mcp", content=json.dumps(body), headers={**HEADERS, **headers})
        return response.status, response.json if response.status == 200 else {}

    def tools(self, headers: dict[str, str]) -> list[str]:
        status, answer = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers)
        self.assertEqual(200, status)
        return sorted(tool["name"] for tool in answer["result"]["tools"])

    def test_users_see_what_their_permissions_allow_and_the_api_key_sees_only_example_caller(self) -> None:
        superuser = {"Authorization": f"Bearer {issue_access_token(SUPERUSER).token}"}
        plain = {"Authorization": f"Bearer {issue_access_token(PLAIN_USER).token}"}
        key = {"X-API-Key": settings.web.auth.api_keys[DEMO_API_KEY_NAME].secret}
        self.assertEqual(["count_example_projects_by_status", "example_caller", "find_example_projects"], self.tools(superuser))
        self.assertEqual(["example_caller"], self.tools(plain))
        self.assertEqual(["example_caller"], self.tools(key))
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "example_caller", "arguments": {}}}
        status, answer = self.post(call, key)
        self.assertEqual(200, status)
        self.assertEqual({"method": "api_key", "user": None, "caller": DEMO_API_KEY_NAME}, answer["result"]["structuredContent"])
        self.assertEqual(401, self.post(call, {})[0])


if __name__ == "__main__":
    unittest.main()
