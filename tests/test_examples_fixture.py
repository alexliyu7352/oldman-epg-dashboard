"""End-to-end contracts for the Dashboard's single standard fixture."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests.test_epg_dashboard_migrations import (
    copy_epg_dashboard,
    run_cli,
    run_python,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "apps/examples/fixtures/demo.json"


def migrate_project(project: Path):
    """Apply the copied project's real migration graph non-interactively."""
    return run_python(
        project,
        """
        from pathlib import Path

        from oldman.db.migrations.commands import migrate
        from oldman.db.migrations.project import load_migration_project

        project = load_migration_project(Path.cwd())

        class Answers:
            is_interactive = False

            def choose(self, prompt, choices):
                if "internal migration state" in prompt:
                    return "first use"
                if "migration scope" in prompt:
                    return "all"
                raise AssertionError((prompt, choices))

            def confirm(self, prompt, *, default=False):
                raise AssertionError(prompt)

            def text(self, prompt, *, default):
                raise AssertionError(prompt)

        migrate(project, Answers())
        """,
    )


class ExampleFixtureTests(unittest.TestCase):
    """Treat committed example data as a reproducible public contract."""

    def test_fixture_has_the_documented_models_and_committed_logos(self) -> None:
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        counts = Counter(record["model"] for record in records)

        self.assertEqual(8, counts["examples.ExampleTeam"])
        self.assertEqual(120, counts["examples.ExampleProject"])
        self.assertEqual(360, counts["examples.ExampleTask"])
        self.assertEqual(16, counts["examples.ExampleTag"])
        self.assertEqual(240, counts["examples.ExampleProjectTag"])
        self.assertEqual(4, counts["examples.ExampleServer"])
        self.assertEqual(288, counts["examples.ExampleServerMetric"])
        self.assertGreaterEqual(counts["examples.ExampleLogo"], 48)
        self.assertGreaterEqual(counts["examples.ExampleStreamProfile"], 12)
        self.assertEqual(0, counts["examples.ExampleAsset"])
        self.assertEqual(12, counts["epg_admin.ChannelsEpg"])
        self.assertEqual(36, counts["epg_admin.EpgList"])
        self.assertEqual(12, counts["epg_admin.ChannelName"])
        self.assertEqual(12, counts["epg_admin.CatalogChannel"])
        self.assertEqual(12, counts["epg_admin.CatalogFeed"])
        self.assertEqual(12, counts["epg_admin.UpstreamSourceRecord"])
        self.assertEqual(12, counts["epg_admin.CatalogLogoAsset"])
        self.assertEqual(12, counts["epg_admin.CatalogMatchDecision"])

        logo_paths = {
            record["fields"]["svg_path"]
            for record in records
            if record["model"] == "examples.ExampleLogo"
        }
        self.assertGreaterEqual(len(logo_paths), 12)
        for static_path in logo_paths:
            self.assertTrue(static_path.startswith("/static/examples/logos/"))
            asset = ROOT / "apps/examples/static" / static_path.removeprefix("/static/")
            self.assertTrue(asset.is_file(), asset)

    def test_clean_database_load_is_idempotent_deterministic_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project = copy_epg_dashboard(Path(temporary_directory))
            synced = run_cli(project, "web", "settings", "sync")
            migrated = migrate_project(project)
            first_load = run_cli(project, "web", "loaddata", "demo")
            second_load = run_cli(project, "web", "loaddata", "demo")
            first_dump = project / "first.json"
            second_dump = project / "second.json"
            dumped_once = run_cli(
                project,
                "web",
                "dumpdata",
                "examples",
                "--output",
                str(first_dump),
            )
            dumped_twice = run_cli(
                project,
                "web",
                "dumpdata",
                "examples",
                "--output",
                str(second_dump),
            )

            database = project / "data/epg_dashboard.db"
            with sqlite3.connect(database) as connection:
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "example_team",
                        "example_project",
                        "example_task",
                        "example_project_tag",
                        "example_server_metric",
                        "example_logo",
                        "example_stream_profile",
                        "epg_channelsepg",
                        "epg_epglist",
                        "epg_channelname",
                        "catalog_channel",
                        "catalog_feed",
                        "upstream_source_record",
                        "catalog_logo_asset",
                        "catalog_match_decision",
                    )
                }
                team = connection.execute(
                    "SELECT name, slug FROM example_team WHERE id = 1"
                ).fetchone()
                cursor = connection.execute(
                    "INSERT INTO example_team (name, slug, region, is_active) VALUES (?, ?, ?, ?)",
                    ("Autoincrement Team", "autoincrement-team", "test", True),
                )
                generated_team_id = cursor.lastrowid
                connection.commit()

            broken_fixture = project / "broken.json"
            broken_fixture.write_text(
                json.dumps(
                    [
                        {
                            "model": "examples.ExampleTeam",
                            "pk": 1,
                            "fields": {"name": "Must Roll Back"},
                        },
                        {
                            "model": "examples.ExampleTeam",
                            "pk": 999,
                            "fields": {
                                "name": "Duplicate Slug",
                                "slug": team[1],
                                "region": "test",
                                "is_active": True,
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )
            failed = run_cli(project, "web", "loaddata", str(broken_fixture))
            first_dump_bytes = first_dump.read_bytes()
            second_dump_bytes = second_dump.read_bytes()
            with sqlite3.connect(database) as connection:
                rolled_back_name = connection.execute(
                    "SELECT name FROM example_team WHERE id = 1"
                ).fetchone()[0]
                rejected_count = connection.execute(
                    "SELECT COUNT(*) FROM example_team WHERE id = 999"
                ).fetchone()[0]

        for completed in (
            synced,
            migrated,
            first_load,
            second_load,
            dumped_once,
            dumped_twice,
        ):
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertNotEqual(0, failed.returncode, failed.stdout + failed.stderr)
        self.assertEqual(first_dump_bytes, second_dump_bytes)
        self.assertEqual(8, counts["example_team"])
        self.assertEqual(120, counts["example_project"])
        self.assertEqual(360, counts["example_task"])
        self.assertEqual(240, counts["example_project_tag"])
        self.assertEqual(288, counts["example_server_metric"])
        self.assertGreaterEqual(counts["example_logo"], 48)
        self.assertGreaterEqual(counts["example_stream_profile"], 12)
        self.assertEqual(12, counts["epg_channelsepg"])
        self.assertEqual(36, counts["epg_epglist"])
        self.assertEqual(12, counts["epg_channelname"])
        self.assertEqual(12, counts["catalog_channel"])
        self.assertEqual(12, counts["catalog_feed"])
        self.assertEqual(12, counts["upstream_source_record"])
        self.assertEqual(12, counts["catalog_logo_asset"])
        self.assertEqual(12, counts["catalog_match_decision"])
        self.assertEqual(9, generated_team_id)
        self.assertEqual(team[0], rolled_back_name)
        self.assertEqual(0, rejected_count)


if __name__ == "__main__":
    unittest.main()
