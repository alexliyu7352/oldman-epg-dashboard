"""EPG notification browser wrapper isolation and lifecycle tests."""

from __future__ import annotations

import base64
import importlib.util
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-notifications-browser-with-server.py"


def load_wrapper() -> ModuleType:
    """Load the hyphenated wrapper as a normal Python module."""
    spec = importlib.util.spec_from_file_location(
        "verify_epg_notifications_browser_with_server",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EpgNotificationBrowserWrapperTest(unittest.TestCase):
    """Keep the focused gate on one owned Dashboard runtime."""

    def test_settings_use_owned_database_static_and_distinct_redis_databases(self) -> None:
        wrapper = load_wrapper()
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_root = Path(temporary_directory)
            config_file = wrapper.prepare_gate_settings(
                state_root,
                redis_url="redis://127.0.0.1:47339",
                service_port=39117,
            )
            payload = YAML(typ="safe", pure=True).load(
                config_file.read_text(encoding="utf-8")
            )

        self.assertEqual(
            payload["database"]["url"],
            f"sqlite+aiosqlite:///{state_root / 'dashboard.sqlite3'}",
        )
        self.assertEqual(
            payload["redis"]["SESSION"]["redis_url"],
            "redis://127.0.0.1:47339/0",
        )
        self.assertEqual(
            payload["redis"]["SSE"]["redis_url"],
            "redis://127.0.0.1:47339/1",
        )
        self.assertTrue(payload["web"]["sse"]["enabled"])
        self.assertFalse(payload["taskiq"]["enabled"])
        self.assertFalse(payload["nats_bus"]["enabled"])
        self.assertEqual(payload["web"]["listen_port"], 39117)
        self.assertEqual(payload["web"]["static"]["root"], str(state_root / "static"))
        self.assertIn("oldman.web.messages.notifications", payload["apps"])
        fingerprint_key = base64.b64decode(
            payload["web"]["security"]["fingerprint"]["aes_secret_key"],
            validate=True,
        )
        self.assertEqual(len(fingerprint_key), 32)

    def test_gate_orders_migration_account_service_browser_and_owned_cleanup(self) -> None:
        wrapper = load_wrapper()
        events: list[str] = []

        @contextmanager
        def redis_server(_state_root: Path, *, environment: dict[str, str]):
            del environment
            events.append("redis:start")
            try:
                yield "redis://127.0.0.1:47339"
            finally:
                events.append("redis:stop")

        @contextmanager
        def service(_environment: dict[str, str], _config_file: Path):
            events.append("service:start")
            try:
                yield object()
            finally:
                events.append("service:stop")

        def record(name: str, result=None):
            def callback(*_args, **_kwargs):
                events.append(name)
                return result

            return callback

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(wrapper, "owned_redis_server", side_effect=redis_server),
            patch.object(wrapper, "collect_gate_static", side_effect=record("static")),
            patch.object(
                wrapper,
                "prepare_gate_settings",
                side_effect=record("settings", Path(temporary_directory) / "web.yaml"),
            ),
            patch.object(wrapper, "migrate_gate_database", side_effect=record("migrate")),
            patch.object(wrapper, "ensure_gate_admin", side_effect=record("admin")),
            patch.object(wrapper, "owned_service", side_effect=service),
            patch.object(wrapper, "wait_for_service", side_effect=record("ready")),
            patch.object(wrapper, "run_browser_gate", side_effect=record("browser", 0)),
        ):
            result = wrapper.run_notification_gate(
                "chrome",
                Path(temporary_directory),
                source_environment={"PATH": "/usr/bin", "HOME": "/tmp"},
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            events,
            [
                "redis:start",
                "static",
                "settings",
                "migrate",
                "admin",
                "service:start",
                "ready",
                "browser",
                "service:stop",
                "redis:stop",
            ],
        )

    def test_wrapper_does_not_reuse_the_comprehensive_clean_tree_gate(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("git status", source)
        self.assertNotIn("copytree", source)
        self.assertIn("verify-notifications-browser.py", source)


if __name__ == "__main__":
    unittest.main()
