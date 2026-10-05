"""Dashboard Table 示例的查询、表单和路由合同。"""

from __future__ import annotations

import asyncio
import datetime as dt
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import Select


class ExampleTableTests(unittest.TestCase):
    """验证三种 Table 共用真实 ExampleProject 数据和 CRUD。"""

    def test_project_table_uses_database_filters_and_stable_row_ids(self) -> None:
        from apps.examples.tables import ExampleProjectTable

        table = ExampleProjectTable()
        query = asyncio.run(table.get_queryset())
        filtered = asyncio.run(table.filter_status(query, "active", SimpleNamespace()))

        self.assertIsInstance(query, Select)
        self.assertIn("example_project", str(filtered))
        self.assertIn("example_project.status", str(filtered))
        self.assertEqual(17, table.get_row_id(SimpleNamespace(id=17)))
        self.assertTrue(table.selectable)

    def test_project_form_validates_business_fields(self) -> None:
        from apps.examples.forms import ExampleProjectForm
        from apps.examples.models import ExampleTeam

        choice_result = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [ExampleTeam(id=1, name="Team", slug="team", region="test")])
        )
        session = SimpleNamespace(execute=AsyncMock(return_value=choice_result))

        invalid = ExampleProjectForm(
            data={
                "team_id": "1",
                "name": "",
                "slug": "Bad Slug",
                "status": "active",
                "priority": "normal",
                "progress": "101",
                "is_active": "y",
            },
            session=session,
        )

        self.assertFalse(asyncio.run(invalid.validate()))
        self.assertIn("name", invalid.errors)
        self.assertIn("slug", invalid.errors)
        self.assertIn("progress", invalid.errors)

    def test_realtime_payload_uses_server_ids_and_table_column_names(self) -> None:
        from apps.examples.services import _realtime_payload

        sampled_at = dt.datetime(2026, 9, 1, 8, 20, 17)
        payload = _realtime_payload(
            [
                (
                    SimpleNamespace(id=7),
                    SimpleNamespace(
                        sampled_at=sampled_at,
                        cpu_percent=Decimal("37.25"),
                        memory_percent=Decimal("48.50"),
                        upload_mbps=Decimal("61.75"),
                        download_mbps=Decimal("84.00"),
                    ),
                )
            ]
        )

        self.assertEqual(
            {
                "rows": [
                    {
                        "id": 7,
                        "cells": {
                            "sampled_at": "2026-09-01 08:20:17",
                            "cpu_percent": "37.25%",
                            "memory_percent": "48.50%",
                            "upload_mbps": "61.75 Mbps",
                            "download_mbps": "84.00 Mbps",
                        },
                    }
                ]
            },
            payload.to_dict(),
        )

class ProjectTablePermissionTests(unittest.IsolatedAsyncioTestCase):
    """The one RBAC example: signing in (even as staff) does not open the project table, a role granting view_projects does."""

    async def test_the_project_table_needs_a_role_granting_view_projects(self) -> None:
        from oldman.web.authentication import RequestUser

        from apps.examples.tables import ExampleProjectTable

        # This service installs the roles App; permission checks only read roles where it is installed.
        app = SimpleNamespace(ctx=SimpleNamespace(app_registry=SimpleNamespace(labels=("auth", "roles", "examples"))))

        def request_for(**user: object) -> SimpleNamespace:
            return SimpleNamespace(app=app, ctx=SimpleNamespace(user=RequestUser(id=7, username="ops", is_staff=True, **user)))  # type: ignore[arg-type]

        table = ExampleProjectTable()
        def allowed(request):
            return table.check_permission(request, method_name="get", route_kwargs={})

        self.assertEqual((False, None), await allowed(request_for()))
        self.assertEqual((True, None), await allowed(request_for(is_superuser=True)))
        grants = AsyncMock(return_value=frozenset({"examples.view_projects"}))
        with patch("oldman.apps.roles.store.role_permissions", grants):
            self.assertEqual((True, None), await allowed(request_for(role_ids=(1,))))
        # Roles missing from the cache are read through the given db_manager; None means the process's own.
        grants.assert_awaited_once_with((1,), db_manager=None)


class TeamProjectTableAccessTests(unittest.IsolatedAsyncioTestCase):
    """The class-based guard example: each hook of the team table's data endpoint answers its own question."""

    async def asyncSetUp(self) -> None:
        from typing import cast

        from sqlalchemy import Table, insert
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.examples.models import ExampleProject, ExampleTeam

        teams, projects = cast(Table, ExampleTeam.__table__), cast(Table, ExampleProject.__table__)

        self.engine = create_async_engine("sqlite+aiosqlite://")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            for table in (teams, projects):
                await connection.run_sync(table.create)
            await connection.execute(
                insert(teams),
                [
                    {"id": 1, "name": "Active", "slug": "active", "region": "eu", "is_active": True},
                    {"id": 2, "name": "Other", "slug": "other", "region": "us", "is_active": True},
                    {"id": 3, "name": "Gone", "slug": "gone", "region": "ca", "is_active": False},
                ],
            )
            await connection.execute(
                insert(projects),
                [
                    {"id": 11, "team_id": 1, "name": "Alpha", "slug": "alpha"},
                    {"id": 12, "team_id": 1, "name": "Beta", "slug": "beta"},
                    {"id": 21, "team_id": 2, "name": "Gamma", "slug": "gamma"},
                    {"id": 31, "team_id": 3, "name": "Delta", "slug": "delta"},
                ],
            )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def fetch(self, user, team_id: int, *, database=None):
        """Dispatch the data endpoint the way its route does, with a JSON client."""
        from typing import Any, cast

        from apps.examples.tables import TeamProjectTable

        table = TeamProjectTable()
        table.database_manager = cast(Any, database or SimpleNamespace(get_read_session=self.sessions))
        request = SimpleNamespace(
            app=SimpleNamespace(ctx=SimpleNamespace()),
            args={},
            headers={"accept": "application/json"},
            method="GET",
            path=f"/examples/auth/teams/{team_id}/projects/table",
            ctx=SimpleNamespace(user=user),
        )
        return await table.dispatch_request(request, team_id=team_id)

    async def test_an_anonymous_request_is_asked_to_sign_in(self) -> None:
        from oldman.web.authentication import ANONYMOUS_USER

        self.assertEqual(401, (await self.fetch(ANONYMOUS_USER, 1)).status)

    async def test_check_permission_refuses_a_member_before_any_query(self) -> None:
        from oldman.web.authentication import RequestUser

        no_database = SimpleNamespace(get_read_session=Mock(side_effect=AssertionError("a refusal decided from the user opens no session")))
        response = await self.fetch(RequestUser(id=5, username="member"), 1, database=no_database)

        self.assertEqual(403, response.status)
        no_database.get_read_session.assert_not_called()

    async def test_check_auth_refuses_staff_an_inactive_or_missing_team(self) -> None:
        from oldman.web.authentication import RequestUser

        staff = RequestUser(id=1, username="ops", is_staff=True)
        for team_id in (3, 99):
            with self.subTest(team_id=team_id):
                self.assertEqual(403, (await self.fetch(staff, team_id)).status)

    async def test_apply_base_filters_shows_staff_only_the_team_in_the_path(self) -> None:
        import json

        from oldman.web.authentication import RequestUser

        response = await self.fetch(RequestUser(id=1, username="ops", is_staff=True), 1)
        payload = json.loads(response.body)

        self.assertEqual(200, response.status)
        self.assertEqual({"Alpha", "Beta"}, {row["raw_values"]["name"] for row in payload["rows"]})
        # The rows it leaves out are not counted either: totals and pages stay within the team.
        self.assertEqual(2, payload["pagination"]["total"])
        self.assertNotIn("action", {column["name"] for column in payload["columns"]})


class ProjectEndpointPermissionTests(unittest.IsolatedAsyncioTestCase):
    """Reading a project needs view_projects; creating, saving and deleting one needs change_projects."""

    async def test_each_project_endpoint_asks_for_its_permission_before_touching_data(self) -> None:
        import inspect

        from oldman.web.authentication import RequestUser
        from oldman.web.exceptions import Forbidden

        from apps.examples.views import tables as views

        app = SimpleNamespace(ctx=SimpleNamespace(app_registry=SimpleNamespace(labels=("auth", "roles", "examples"))))

        def staff_request() -> SimpleNamespace:
            # One request per call: a request remembers the permissions it looked up.
            return SimpleNamespace(app=app, ctx=SimpleNamespace(user=RequestUser(id=7, username="ops", is_staff=True, role_ids=(1,))))
        # (handler, needs change_projects); the handlers past their decorators, called with a staff login holding role 1.
        endpoints = (
            (views.example_project_create_modal, True, {}),
            (views.example_project_create, True, {}),
            (views.example_project_edit_modal, False, {"project_id": 5}),
            (views.example_project_update, True, {"project_id": 5}),
            (views.example_project_delete_modal, True, {"project_id": 5}),
            (views.example_project_delete, True, {"project_id": 5}),
        )
        for granted in ({"examples.view_projects"}, {"examples.view_projects", "examples.change_projects"}):
            with (
                patch("oldman.apps.roles.store.role_permissions", AsyncMock(return_value=frozenset(granted))),
                patch.object(views, "db_manager", SimpleNamespace(get_session=Mock(side_effect=LookupError("data")), get_read_session=Mock(side_effect=LookupError("data")))),
            ):
                for handler, needs_change, kwargs in endpoints:
                    with self.subTest(handler=handler.__name__, granted=sorted(granted)):
                        refused = needs_change and "examples.change_projects" not in granted
                        # A refusal comes before the database is touched; an allowed call reaches it.
                        with self.assertRaises(Forbidden if refused else LookupError):
                            await inspect.unwrap(handler)(staff_request(), **kwargs)


class RealtimeTableReadTests(unittest.IsolatedAsyncioTestCase):
    """Use a real isolated database to check replay limits and read-only behavior."""

    async def test_replay_is_bounded_complete_and_does_not_insert_samples(self) -> None:
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from apps.examples import services
        from apps.examples.models import ExampleServer, ExampleServerMetric

        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                for table in (ExampleServer.__table__, ExampleServerMetric.__table__):
                    await connection.run_sync(table.create)
            with patch.object(services, "db_manager", SimpleNamespace(get_read_session=sessions)):
                self.assertEqual([], await services.list_server_metric_snapshots())
                origin = dt.datetime(2026, 9, 1)
                async with sessions.begin() as session:
                    session.add_all([
                        ExampleServer(id=i, name=f"Server {i}", host=f"server-{i}", region="test",
                                      cpu_cores=4, memory_gb=8, bandwidth_mbps=100)
                        for i in (1, 2)
                    ])
                    await session.flush()
                    session.add_all([
                        ExampleServerMetric(server_id=i, sampled_at=origin + dt.timedelta(seconds=t),
                                            cpu_percent=t, memory_percent=50, upload_mbps=10, download_mbps=20)
                        for t in range(66) for i in (1, 2) if t < 65 or i == 1
                    ])
                # The actual chart writer adds newer partial batches, not table replay data.
                for _ in range(60):
                    async with sessions.begin() as session:
                        await services.create_server_metric(session, 1)
                # Two viewers still see 60 complete stored batches without inserting samples.
                for _ in range(2):
                    batches = await services.list_server_metric_snapshots()
                    self.assertEqual(60, len(batches))
                    self.assertEqual(origin + dt.timedelta(seconds=5), batches[0][0][1].sampled_at)
                    self.assertEqual(origin + dt.timedelta(seconds=64), batches[-1][0][1].sampled_at)
                    self.assertTrue(all({server.id for server, _metric in batch} == {1, 2} for batch in batches))
                    self.assertNotEqual(services._realtime_payload(batches[0]), services._realtime_payload(batches[1]))
                async with sessions() as session:
                    self.assertEqual(191, await session.scalar(select(func.count()).select_from(ExampleServerMetric)))
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
