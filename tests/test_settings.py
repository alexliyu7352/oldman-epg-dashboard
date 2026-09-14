"""Tests for typed application settings."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config.schemas import BASE_DIR, Settings
from config.settings import settings

from oldman.auth.apps import app as auth_app
from oldman.conf.manager import SettingsManager
from oldman.runtime.discovery import get_service_definition

ROOT = Path(__file__).resolve().parents[1]


class SettingsTest(unittest.TestCase):
    """Verify the default settings used by the application skeleton."""

    def test_application_name_defaults_to_oldman(self) -> None:
        """The app name should match the package and Sanic application name."""
        self.assertEqual(settings.core.app_name, "oldman")

    def test_template_directory_defaults_to_templates(self) -> None:
        """Template paths should point at the conventional templates directory."""
        self.assertEqual(settings.web.template.dir.name, "templates")

    def test_static_directory_defaults_to_static(self) -> None:
        """Static paths should point at the conventional static directory."""
        self.assertEqual(settings.web.static.dir.name, "static")

    def test_database_defaults_to_isolated_local_sqlite(self) -> None:
        """The schema default must not require an external database server."""
        expected_path = BASE_DIR / "data" / "epg_dashboard.db"
        default_settings = Settings()

        self.assertEqual(
            default_settings.database.url,
            f"sqlite+aiosqlite:///{expected_path.as_posix()}",
        )

    def test_auth_uses_the_example_user_model(self) -> None:
        """Authentication and Admin must target the same project User table."""
        self.assertEqual(
            auth_app.settings.user_model,
            "apps.auth.models.OldmanUser",
        )

    def test_session_uses_named_redis_with_the_dashboard_contract(self) -> None:
        """The runnable service must use the configured Redis Session backend."""
        self.assertTrue(settings.web.session.enabled)
        self.assertEqual(settings.web.session.redis_alias, "SESSION")
        self.assertEqual(settings.web.session.prefix, "oldman_session:")
        self.assertEqual(settings.web.session.user_prefix, "oldman_user_session:")
        self.assertEqual(settings.web.session.cookie_name, "oldman_session_id")
        self.assertTrue(settings.redis.SESSION.redis_url.startswith("redis://"))

    def test_dashboard_languages_come_from_i18n_language_settings(self) -> None:
        """Dashboard language menu data should use the shared Oldman language config."""
        from services.web import dashboard_language_items, dashboard_supported_languages

        self.assertEqual(dashboard_supported_languages(), list(settings.i18n.languages))

        items = dashboard_language_items()
        self.assertEqual([item["code"] for item in items], list(settings.i18n.languages))
        self.assertEqual(items[1]["name"], "简体中文")
        self.assertEqual(
            items[1]["flag_asset"],
            "/static/oldman/images/flags/cn.svg",
        )

    def test_dashboard_language_aliases_are_settings_driven(self) -> None:
        """Configured aliases and locales should resolve to the dashboard canonical code."""
        from services.web import normalize_dashboard_language

        self.assertEqual(normalize_dashboard_language("zh-CN"), "zh-Hans")
        self.assertEqual(normalize_dashboard_language("zh-Hans"), "zh-Hans")
        self.assertEqual(normalize_dashboard_language("zh-HK"), "zh-Hant")
        self.assertEqual(normalize_dashboard_language("zh_Hans"), "")

    def test_settings_yaml_is_local_runtime_config(self) -> None:
        """本地配置和 SQLite 运行数据不能被加入 Git 仓库。"""
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        example_settings = (ROOT / "data" / "web_settings.example.yaml").read_text(encoding="utf-8")
        tracked_files = subprocess.run(
            ["git", "ls-files", "data/web_settings.yaml"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("/data/web_settings.yaml", gitignore)
        self.assertIn("/data/*.db", gitignore)
        self.assertIn("sqlite+aiosqlite:///data/epg_dashboard.db", example_settings)
        self.assertIn("user_model: apps.auth.models.OldmanUser", example_settings)
        self.assertNotIn("mysql+aiomysql", example_settings)
        self.assertEqual(tracked_files.stdout.strip(), "")

    def test_i18n_languages_must_not_have_schema_defaults(self) -> None:
        """I18n language config must not be generated from the old DEFAULT_I18N_LANGUAGES list."""
        source = (ROOT / "config" / "schemas.py").read_text(encoding="utf-8")

        self.assertNotIn("DEFAULT_I18N_LANGUAGES", source)
        self.assertNotIn("default_i18n_languages", source)

    def test_importing_schema_does_not_require_settings_yaml(self) -> None:
        """Importing config schemas must not check for settings.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            temp_config_dir = temp_root / "config"
            temp_config_dir.mkdir()
            (temp_config_dir / "__init__.py").write_text("", encoding="utf-8")
            (temp_config_dir / "schemas.py").write_text((ROOT / "config" / "schemas.py").read_text(encoding="utf-8"), encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONPATH"] = f"{temp_root}{os.pathsep}{ROOT}"
            completed = subprocess.run(
                [sys.executable, "-c", "import config.schemas"],
                cwd=temp_root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def test_bootstrap_accepts_an_explicit_settings_path_for_tools(self) -> None:
        """IDE and test tools may bootstrap one explicit service YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            settings_file = Path(tmp) / "web_settings.yaml"
            settings_file.write_text(
                (ROOT / "data" / "web_settings.example.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (str(ROOT), env.get("PYTHONPATH")),
                )
            )
            synced = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "oldman.cli",
                    "web",
                    "settings",
                    "sync",
                    "--config",
                    str(settings_file),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    ("import sys; from oldman import bootstrap_service; print(bootstrap_service('web', config_file=sys.argv[1]).config_file)"),
                    str(settings_file),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(synced.returncode, 0, synced.stderr + synced.stdout)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(settings_file.resolve(), Path(completed.stdout.strip()))

    def test_runtime_settings_refuses_missing_settings_yaml(self) -> None:
        """Runtime settings access must fail clearly when settings.yaml is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            missing_settings = Path(tmp) / "settings.yaml"
            manager = SettingsManager(
                Settings,
                get_service_definition("web", ROOT),
                missing_settings,
            )

            with self.assertRaisesRegex(RuntimeError, "settings.yaml 不存在"):
                manager.load()


if __name__ == "__main__":
    unittest.main()
