"""Dashboard browser gate wrapper tests."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
repository_path = str(ROOT)
while repository_path in sys.path:
    sys.path.remove(repository_path)
sys.path.insert(0, repository_path)
SCRIPT_PATH = ROOT / "scripts" / "verify-dashboard-browser-with-server.py"


def load_wrapper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_dashboard_browser_with_server", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load dashboard browser wrapper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DashboardBrowserWrapperTest(unittest.TestCase):
    """Ensure the server wrapper runs all required browser gates."""

    def test_wrapper_runs_visual_interaction_and_tailwind_gates(self) -> None:
        wrapper = load_wrapper()
        script_names = [path.name for path in wrapper.browser_gate_scripts()]

        self.assertIn("verify-dashboard-browser.py", script_names)
        self.assertIn("verify-examples-browser.py", script_names)
        self.assertIn("verify-tailwind-browser.py", script_names)

    def test_wrapper_loads_the_standard_example_fixture_before_starting(self) -> None:
        wrapper = load_wrapper()
        env = {"OLDMAN_GATE_CONFIG_FILE": "/tmp/example-gate.yaml"}

        with patch.object(wrapper.subprocess, "run") as run:
            wrapper.load_gate_fixture(env)

        command = run.call_args.args[0]
        self.assertIn("LoadData()", command[2])
        self.assertEqual("/tmp/example-gate.yaml", command[3])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_wrapper_isolates_uploaded_media_with_the_gate_database(self) -> None:
        wrapper = load_wrapper()
        with tempfile.TemporaryDirectory(prefix="oldman-gate-settings-") as temp_dir:
            state_root = Path(temp_dir)
            env = {
                "OLDMAN_DASHBOARD_URL": "http://127.0.0.1:17998/",
                "OLDMAN_GATE_CONFIG_FILE": str(state_root / "web_settings.yaml"),
                "OLDMAN_GATE_STATIC_ROOT": str(state_root / "static"),
            }

            config_file = wrapper.prepare_gate_settings(env, state_root, redis_url="redis://127.0.0.1:6380/0")
            payload = wrapper.YAML(typ="safe", pure=True).load(config_file.read_text(encoding="utf-8"))

        self.assertEqual(str(state_root / "media"), payload["storages"]["default"]["options"]["location"])
        self.assertFalse(payload["taskiq"]["enabled"])
        self.assertFalse(payload["nats_bus"]["enabled"])

    def test_wrapper_no_longer_describes_service_as_legacy_package(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertNotIn("ac" + "_base web 服务", source)


if __name__ == "__main__":
    unittest.main()
