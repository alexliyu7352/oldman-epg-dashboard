#!/usr/bin/env python3
"""Run the focused EPG notification gate against owned local resources."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for import_root in (str(ROOT),):
    while import_root in sys.path:
        sys.path.remove(import_root)
    sys.path.insert(0, import_root)

from oldman.testing.gates import (  # noqa: E402
    DEFAULT_HOST,
    ensure_gate_admin,
    find_free_port,
    gate_settings,
    migrate_gate_database,
    minimal_environment,
    owned_redis_server,
    owned_service,
    run_browser_child,
    wait_for_service,
)

DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
GATE_NAMESPACE = "oldman_epg_notification_gate"


def prepare_gate_settings(state_root: Path, *, redis_url: str, service_port: int) -> Path:
    """The shared gate settings plus this demo's own templates, assets and disabled task buses."""

    def customize(payload: dict) -> None:
        # Notification isolation must not connect to the Demo's configured task infrastructure.
        payload["taskiq"]["enabled"] = False
        payload["nats_bus"]["enabled"] = False
        payload["web"]["template"]["dir"] = str(ROOT / "templates")

    return gate_settings(
        ROOT / "data" / "web_settings.example.yaml",
        state_root,
        service_port=service_port,
        redis_url=redis_url,
        namespace=GATE_NAMESPACE,
        database_name="dashboard.sqlite3",
        static_dir=ROOT / "static",
        customize=customize,
    )


def collect_gate_static(state_root: Path) -> None:
    """Collect the built EPG bundle and framework assets into owned output."""
    from oldman.web.staticfiles import collect_project_static

    collect_project_static(
        project_directory=ROOT / "static",
        destination=state_root / "static",
        clear=True,
    )


def load_gate_fixture(
    environment: Mapping[str, str],
    config_file: Path,
) -> None:
    """Load the public demo fixture needed by the Firefox examples smoke."""
    source = (
        "import sys; "
        "from pathlib import Path; "
        "from oldman import bootstrap_service; "
        "from oldman.cli.fixtures import LoadData; "
        "from oldman.runtime.discovery import get_service_definition, load_service_class; "
        "bootstrap_service('web', config_file=sys.argv[1]); "
        "definition = get_service_definition('web', Path.cwd()); "
        "service = load_service_class(definition); "
        "service.execute_app_command(LoadData(), 'demo')"
    )
    subprocess.run(
        [sys.executable, "-c", source, str(config_file)],
        cwd=ROOT,
        env=dict(environment),
        check=True,
    )


def run_browser_gate(
    browser: str,
    environment: Mapping[str, str],
    config_file: Path,
    service_port: int,
) -> int:
    """Run this demo's notification browser child through the shared strict check."""
    run_browser_child(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify-notifications-browser.py"),
            "--browser",
            browser,
            "--url",
            f"http://{DEFAULT_HOST}:{service_port}",
            "--config",
            str(config_file),
            "--username",
            DEFAULT_USERNAME,
            "--password",
            DEFAULT_PASSWORD,
        ],
        environment=environment,
        project_root=ROOT,
        browser=browser,
        label="EPG notification",
    )
    return 0


def run_notification_gate(
    browser: str,
    state_root: Path,
    *,
    source_environment: Mapping[str, str] | None = None,
) -> int:
    """Assemble and execute the focused EPG browser lifecycle."""
    environment = minimal_environment(source_environment)
    service_port = find_free_port()
    with owned_redis_server(
        state_root / "redis",
        environment=environment,
    ) as redis_url:
        collect_gate_static(state_root)
        config_file = prepare_gate_settings(
            state_root,
            redis_url=redis_url,
            service_port=service_port,
        )
        migrate_gate_database(config_file, state_root, project_root=ROOT)
        if browser == "firefox":
            load_gate_fixture(environment, config_file)
        ensure_gate_admin(
            config_file,
            environment=environment,
            project_root=ROOT,
            username=DEFAULT_USERNAME,
            password=DEFAULT_PASSWORD,
        )
        with owned_service(config_file, environment=environment, project_root=ROOT, name="epg-service") as process:
            wait_for_service(process, service_port, name="EPG service")
            return run_browser_gate(
                browser,
                environment,
                config_file,
                service_port,
            )


def main() -> int:
    """Parse the selected real browser and run against temporary state."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=("chrome", "firefox"), default="chrome")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="oldman-epg-notification-gate-"
    ) as temporary_directory:
        return run_notification_gate(args.browser, Path(temporary_directory))


if __name__ == "__main__":
    raise SystemExit(main())
