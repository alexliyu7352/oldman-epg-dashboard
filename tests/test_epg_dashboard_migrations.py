"""EPG Dashboard installation through App Registry and project migrations."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from oldman.testing import owned_redis_server, use_owned_redis
from oldman.testing.gates import ensure_gate_admin

ROOT = Path(__file__).resolve().parents[1]
EPG_DASHBOARD = ROOT


def copy_epg_dashboard(destination: Path, *, redis_url: str) -> Path:
    """Copy the Python consumer sources needed by an isolated installation.

    Every copied service's Redis aliases point at the test's own server: the steps below run
    real processes, and the example settings name the developer's Redis.
    """
    project = destination / "epg_dashboard"
    project.mkdir()
    for directory in ("apps", "config", "services"):
        shutil.copytree(EPG_DASHBOARD / directory, project / directory)
    (project / "data").mkdir()
    (project / "scripts").mkdir()
    shutil.copy2(EPG_DASHBOARD / "pyproject.toml", project / "pyproject.toml")
    # 迁移要求每个已声明的服务都有自己的 settings 文件，只复制 web 的话命令在读配置前就退出了。
    for example in sorted((EPG_DASHBOARD / "data").glob("*_settings.example.yaml")):
        shutil.copy2(example, project / "data" / example.name.replace(".example", ""))

    for config_path in sorted((project / "data").glob("*_settings.yaml")):
        payload = YAML(typ="safe", pure=True).load(config_path.read_text(encoding="utf-8"))
        use_owned_redis(payload, redis_url)
        if config_path.name == "web_settings.yaml":
            payload["i18n"]["use_i18n"] = False
        with config_path.open("w", encoding="utf-8") as file:
            YAML().dump(payload, file)
    return project


def project_environment(project: Path) -> dict[str, str]:
    """Return deterministic imports and CLI localization for a copied demo."""
    environment = os.environ.copy()
    paths = [str(project)]
    if existing_path := environment.get("PYTHONPATH"):
        paths.append(existing_path)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["OLDMAN_CLI_LANGUAGE"] = "en"
    environment["XDG_CONFIG_HOME"] = str(project / ".cli-config")
    environment["LANG"] = "C"
    return environment


def run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one public Oldman command in the copied project."""
    return subprocess.run(
        [sys.executable, "-m", "oldman.cli", *args],
        cwd=project,
        env=project_environment(project),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_python(project: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Run one programmatic migration or runtime check in isolation."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=project,
        env=project_environment(project),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


class EpgDashboardMigrationTests(unittest.TestCase):
    """Treat the EPG Dashboard as a normal deployable migration consumer."""

    def test_source_contract_registers_apps_without_runtime_schema_creation(self) -> None:
        """Every project package is explicit and deployment owns all DDL."""
        self.assertFalse((EPG_DASHBOARD / "data" / "settings.example.yaml").exists())
        settings_path = EPG_DASHBOARD / "data" / "web_settings.example.yaml"
        settings = YAML(typ="safe", pure=True).load(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(
            settings["apps"],
            [
                "oldman.auth",
                "oldman.apps.admin",
                "oldman.web.messages.notifications",
                "oldman.apps.roles",
                "apps.auth",
                "apps.dashboard",
                "apps.epg_admin",
                "apps.examples",
                "apps.web",
            ],
        )
        for package in ("auth", "dashboard", "epg_admin", "examples", "web"):
            self.assertTrue((EPG_DASHBOARD / "apps" / package / "apps.py").is_file())

        epg_migrations = tuple(path for path in (EPG_DASHBOARD / "apps" / "epg_admin" / "migrations").glob("*.py") if path.name != "__init__.py")
        self.assertEqual(len(epg_migrations), 1)
        example_migrations = tuple(
            path
            for path in (EPG_DASHBOARD / "apps" / "examples" / "migrations").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(len(example_migrations), 1)
        service = (EPG_DASHBOARD / "services" / "web.py").read_text(encoding="utf-8")
        self.assertNotIn("create_db_and_tables", service)
        self.assertNotIn("configure_admin_database", service)
        self.assertNotIn("scan_models", service)
        # 建管理员用框架命令，项目不再自带一份脚本。
        self.assertFalse((EPG_DASHBOARD / "scripts" / "create_admin.py").exists())

    def test_clean_copy_migrates_all_models_and_runtime_stays_lazy(self) -> None:
        """An empty database is migrated before scripts and Web startup use it."""
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            owned_redis_server(Path(temporary_directory) / "redis", environment=os.environ) as redis_url,
        ):
            project = copy_epg_dashboard(Path(temporary_directory), redis_url=redis_url)

            settings_sync = run_cli(project, "web", "settings", "sync")
            history = run_cli(project, "db", "history")
            migrated = run_python(
                project,
                """
                from pathlib import Path

                from oldman.db.migrations.commands import migrate
                from oldman.db.migrations.project import load_migration_project

                project = load_migration_project(Path.cwd())

                class Answers:
                    is_interactive = False

                    def choose(self, prompt, choices):
                        assert "internal migration state" in prompt
                        return "first use"

                    def confirm(self, prompt, *, default=False):
                        raise AssertionError(prompt)

                    def text(self, prompt, *, default):
                        raise AssertionError(prompt)

                migrate(project, Answers())
                """,
            )
            migration_status = run_cli(project, "db", "status")
            # The built-in Admin mounted at /admin serves collected assets and checks for them at startup.
            collected = run_cli(project, "web", "static", "collect")
            runtime = run_python(
                project,
                """
                import asyncio
                from pathlib import Path

                from oldman import bootstrap_service
                from oldman.db import db_manager
                from oldman.runtime.discovery import (
                    get_service_definition,
                    load_service_class,
                )

                async def main():
                    context = bootstrap_service("web")
                    definition = get_service_definition("web", Path.cwd())
                    service_class = load_service_class(definition)
                    service = service_class(
                        context.settings.core.app_name,
                        config=context,
                    )
                    assert not db_manager.is_initialized
                    app = service.create_app()
                    assert not db_manager.is_initialized
                    await service.before_server_start(app)
                    assert not db_manager.is_initialized
                    await service.before_server_stop(app)

                    paths = {
                        route.uri for route in app.router.routes_all.values()
                    }
                    assert "/" in paths, paths
                    assert "/login" in paths, paths
                    assert "/catalog-feeds" in paths, paths
                    assert "/admin" in paths, paths

                asyncio.run(main())
                """,
            )
            # 建管理员走框架的公开路径（bootstrap + ensure_superuser），不启动服务的消息总线。
            create_admin_failure: Exception | None = None
            try:
                ensure_gate_admin(
                    project / "data" / "web_settings.yaml",
                    environment=project_environment(project),
                    project_root=project,
                    username="migration_admin",
                    password="MigrationAdmin123",
                    email="migration@example.com",
                )
            except subprocess.CalledProcessError as exc:  # noqa: PERF203 - reported with the other step results below
                # 裸 CalledProcessError 只说"退出码非 0"；把子进程的输出带上，失败才有诊断价值。
                create_admin_failure = AssertionError(
                    f"ensure_gate_admin failed ({exc.returncode}):\n{exc.stdout or ''}\n{exc.stderr or ''}"
                )
            except Exception as exc:  # noqa: BLE001 - reported with the other step results below
                create_admin_failure = exc

            database = project / "data" / "epg_dashboard.db"
            with sqlite3.connect(database) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                registry = dict(connection.execute("SELECT table_name, app_label FROM oldman_schema_registry"))
                admin_count = connection.execute("SELECT COUNT(*) FROM oldman_user WHERE username='migration_admin'").fetchone()[0]

        self.assertEqual(settings_sync.returncode, 0, settings_sync.stderr)
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertIn("auth:", history.stdout)
        self.assertIn("epg_admin:", history.stdout)
        self.assertIn("examples:", history.stdout)
        self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
        self.assertEqual(collected.returncode, 0, collected.stdout + collected.stderr)
        self.assertEqual(
            migration_status.returncode,
            0,
            migration_status.stdout + migration_status.stderr,
        )
        self.assertIn("examples:", migration_status.stdout)
        self.assertEqual(runtime.returncode, 0, runtime.stdout + runtime.stderr)
        self.assertIsNone(create_admin_failure)
        expected_epg_tables = {
            "epg_channelsepg",
            "epg_epglist",
            "epg_channelname",
            "upstream_source_record",
            "catalog_channel",
            "catalog_feed",
            "catalog_logo_asset",
            "catalog_match_decision",
        }
        expected_example_tables = {
            "example_team",
            "example_project",
            "example_task",
            "example_tag",
            "example_project_tag",
            "example_asset",
            "example_server",
            "example_server_metric",
            "example_logo",
            "example_stream_profile",
        }
        self.assertTrue(expected_epg_tables.issubset(tables))
        self.assertTrue(expected_example_tables.issubset(tables))
        self.assertEqual(registry["oldman_user"], "auth")
        # The roles App carries its own tables and migration.
        self.assertEqual(registry["oldman_role"], "roles")
        self.assertEqual(registry["oldman_user_role"], "roles")
        self.assertEqual(
            {registry[table] for table in expected_epg_tables},
            {"epg_admin"},
        )
        self.assertEqual(
            {registry[table] for table in expected_example_tables},
            {"examples"},
        )
        self.assertEqual(admin_count, 1)


if __name__ == "__main__":
    unittest.main()
