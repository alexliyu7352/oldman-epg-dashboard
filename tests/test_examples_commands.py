"""Run the real App command against an isolated project and SQLite database."""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]


class ProjectStatsCommandTests(unittest.TestCase):
    """Test discovery, argument parsing and actual SQL without starting services."""

    def test_command_reads_and_reports_failures(self) -> None:
        """Counts are fresh and read-only; invalid input and missing tables fail."""
        with tempfile.TemporaryDirectory(prefix="oldman-command-test-", dir="/tmp") as temporary:
            root = Path(temporary)
            for directory in ("apps", "config", "services", "locales"):
                shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
            (root / "data").mkdir()
            settings = YAML(typ="safe", pure=True).load(ROOT / "data/web_settings.example.yaml")
            settings["nats_bus"]["enabled"] = False
            settings["taskiq"]["enabled"] = False
            YAML().dump(settings, root / "data/web_settings.yaml")
            environment = {**os.environ, "OLDMAN_CLI_LANGUAGE": "en", "COLUMNS": "160", "NO_COLOR": "1"}

            def run(*arguments: str, code: int = 0) -> str:
                """Use the public CLI in a separate process, preserving bootstrap isolation."""
                result = subprocess.run(
                    [sys.executable, "-m", "oldman.cli", *arguments],
                    cwd=root, env=environment, capture_output=True, text=True, timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, code, output)
                return output

            run("web", "settings", "sync")
            self.assertIn("project-stats", run("web", "--help"))
            self.assertIn("--team-id", run("web", "project-stats", "--help"))
            database_path = root / "data/epg_dashboard.db"
            for invalid in ("0", "-1", "not-an-id"):
                run("web", "project-stats", "--team-id", invalid, code=2)
            self.assertFalse(database_path.exists())  # Parsing must not open the DB.

            seed = textwrap.dedent("""
                import asyncio
                from oldman import bootstrap_service
                bootstrap_service("web")
                from oldman.db import db_manager
                from sqlalchemy import insert
                from apps.examples.commands import ProjectStats
                from apps.examples.models import ExampleProject, ExampleTeam

                async def main():
                    await db_manager.create_db_and_tables()
                    await ProjectStats().handle()
                    assert db_manager._engine is None
                    async with db_manager.get_session() as session:
                        await session.execute(insert(ExampleTeam), [
                            {"id": i, "name": f"Team {i}", "slug": f"team-{i}", "region": "test"}
                            for i in (1, 2, 3)
                        ])
                        await session.execute(insert(ExampleProject), [
                            {"id": 1, "team_id": 1, "name": "One", "slug": "one", "status": "active"},
                            {"id": 2, "team_id": 1, "name": "Two", "slug": "two", "status": "planned"},
                            {"id": 3, "team_id": 2, "name": "Three", "slug": "three", "status": "active"},
                        ])
                    try:
                        await ProjectStats().handle(team_id=999)
                    except ValueError:
                        assert db_manager._engine is None
                    else:
                        raise AssertionError("Missing team was treated as success")

                asyncio.run(main())
            """)
            seeded = subprocess.run(
                [sys.executable, "-c", seed], cwd=root, env=environment,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(seeded.returncode, 0, seeded.stdout + seeded.stderr)
            self.assertIn("Total projects: 0", seeded.stdout)
            self.assertIn("Total projects: 3\nactive: 2\nplanned: 1", run("web", "project-stats"))
            self.assertIn("Total projects: 2\nactive: 1\nplanned: 1", run("web", "project-stats", "--team-id", "1"))
            self.assertIn("Total projects: 0", run("web", "project-stats", "--team-id", "3"))
            self.assertIn("Team 999 does not exist.", run("web", "project-stats", "--team-id", "999", code=1))
            with closing(sqlite3.connect(database_path)) as database, database:
                self.assertEqual(database.execute("SELECT count(*) FROM example_project").fetchone(), (3,))
                database.execute("UPDATE example_project SET status='planned' WHERE id=3")
            self.assertIn("active: 1\nplanned: 2", run("web", "project-stats"))
            with closing(sqlite3.connect(database_path)) as database, database:
                database.execute("DROP TABLE example_project")
            self.assertIn("no such table", run("web", "project-stats", code=1))
            self.assertIn("no such table", run("web", "project-stats", code=1))  # No automatic repair.
