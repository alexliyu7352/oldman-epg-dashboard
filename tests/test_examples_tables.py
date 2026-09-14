"""Dashboard Table 示例的查询、表单和路由合同。"""

from __future__ import annotations

import asyncio
import datetime as dt
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
