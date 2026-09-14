#!/usr/bin/env python3
"""Run the focused EPG notification gate against owned local resources."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
for import_root in (str(ROOT),):
    while import_root in sys.path:
        sys.path.remove(import_root)
    sys.path.insert(0, import_root)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "CHROME_BIN",
        "DISPLAY",
        "FIREFOX_BIN",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "OLDMAN_CHROME_HEADLESS",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "XAUTHORITY",
    }
)


class BrowserGateError(RuntimeError):
    """Report a focused gate setup, browser, or cleanup failure."""


def minimal_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Retain only host process values needed to launch local tools."""
    environment = {
        name: value
        for name, value in source.items()
        if name in ENVIRONMENT_ALLOWLIST and value
    }
    environment.setdefault("HOME", str(Path.home()))
    environment.setdefault("LANG", "C.UTF-8")
    environment.setdefault("PATH", os.defpath)
    environment["PYTHONHASHSEED"] = "0"
    environment["TZ"] = "UTC"
    return environment


def find_free_port() -> int:
    """Reserve a currently unused loopback port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((DEFAULT_HOST, 0))
        return int(listener.getsockname()[1])


def _redis_database_url(redis_url: str, database: int) -> str:
    """Select one logical database on the gate-owned Redis listener."""
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise BrowserGateError("Gate Redis URL must be a concrete Redis URL")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment)
    )


def prepare_gate_settings(
    state_root: Path,
    *,
    redis_url: str,
    service_port: int,
) -> Path:
    """Write one complete temporary settings file for every gate process."""
    payload = YAML(typ="safe", pure=True).load(
        (ROOT / "data" / "web_settings.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload["core"]["data_dir"] = str(state_root / "data")
    # Notification isolation must not connect to the Demo's configured task infrastructure.
    payload["taskiq"]["enabled"] = False
    payload["nats_bus"]["enabled"] = False
    payload["logging"]["dir"] = str(state_root / "logs")
    payload["process"]["pid_dir"] = str(state_root / "pids")
    payload["database"]["url"] = (
        f"sqlite+aiosqlite:///{state_root / 'dashboard.sqlite3'}"
    )
    payload["redis"]["SESSION"]["redis_url"] = _redis_database_url(
        redis_url, 0
    )
    payload["redis"]["SSE"]["redis_url"] = _redis_database_url(redis_url, 1)
    web = payload["web"]
    web["listen_host"] = DEFAULT_HOST
    web["listen_port"] = service_port
    web["workers"] = 1
    web["access_log"] = False
    web["security"]["secret_key"] = secrets.token_urlsafe(48)
    web["security"]["fingerprint"]["aes_secret_key"] = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    web["session"]["prefix"] = "oldman_epg_notification_gate_session:"
    web["session"]["user_prefix"] = "oldman_epg_notification_gate_user:"
    web["session"]["cookie_name"] = "oldman_epg_notification_gate_sid"
    web["sse"]["heartbeat_interval"] = 0.5
    web["sse"]["session_check_interval"] = 0.5
    web["template"]["dir"] = str(ROOT / "templates")
    web["static"]["dir"] = str(ROOT / "static")
    web["static"]["root"] = str(state_root / "static")
    payload["storages"]["default"]["options"]["location"] = str(
        state_root / "media"
    )

    config_file = state_root / "web_settings.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with config_file.open("w", encoding="utf-8") as output:
        yaml.dump(payload, output)
    return config_file


def collect_gate_static(state_root: Path) -> None:
    """Collect the built EPG bundle and framework assets into owned output."""
    from oldman.web.staticfiles import collect_project_static

    collect_project_static(
        project_directory=ROOT / "static",
        destination=state_root / "static",
        clear=True,
    )


class _FirstUseInteraction:
    """Confirm only the known empty database created by this gate."""

    is_interactive = False

    def choose(self, prompt: str, choices: tuple[str, ...]) -> str:
        if "internal migration state" in prompt and "first use" in choices:
            return "first use"
        raise BrowserGateError(f"Unexpected migration choice: {prompt}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        del default
        raise BrowserGateError(f"Unexpected migration confirmation: {prompt}")

    def text(self, prompt: str, *, default: str) -> str:
        del default
        raise BrowserGateError(f"The browser gate must not generate migrations: {prompt}")


def migrate_gate_database(config_file: Path, state_root: Path) -> None:
    """Apply reviewed project migrations to the gate-owned SQLite database."""
    from oldman.db.migrations.commands import migrate
    from oldman.db.migrations.project import load_migration_project

    project_root = state_root / "migration-project"
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "services").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "pyproject.toml", project_root / "pyproject.toml")
    shutil.copy2(config_file, project_root / "data" / "web_settings.yaml")
    (project_root / "services" / "web.py").write_text(
        "from oldman.runtime.web import WebApplication\n\n"
        "class WebService(WebApplication):\n"
        "    pass\n",
        encoding="utf-8",
    )
    migrate(load_migration_project(project_root), _FirstUseInteraction())


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


def ensure_gate_admin(
    environment: Mapping[str, str],
    config_file: Path,
) -> None:
    """Create the deterministic administrator through public bootstrap APIs."""
    source = """
import asyncio
import sys
from oldman import bootstrap_service

bootstrap_service("web", config_file=sys.argv[1])

from oldman.auth import ensure_superuser
from oldman.auth.apps import app as auth_app
from oldman.db import db_manager

async def main():
    try:
        await ensure_superuser(
            sys.argv[2],
            sys.argv[3],
            "oldman@example.com",
            auth_settings=auth_app.settings,
            db_manager=db_manager,
        )
    finally:
        await db_manager.close()

asyncio.run(main())
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            source,
            str(config_file),
            DEFAULT_USERNAME,
            DEFAULT_PASSWORD,
        ],
        cwd=ROOT,
        env=dict(environment),
        check=True,
    )


def service_command(config_file: Path) -> list[str]:
    """Return the foreground command using the shared bootstrap path."""
    source = (
        "import sys; "
        "from pathlib import Path; "
        "from oldman import bootstrap_service; "
        "from oldman.runtime.discovery import get_service_definition, load_service_class; "
        "bootstrap_service('web', config_file=sys.argv[1]); "
        "definition=get_service_definition('web', Path.cwd()); "
        "service=load_service_class(definition); "
        "service.execute_command('start')"
    )
    return [sys.executable, "-c", source, str(config_file)]


@contextmanager
def owned_service(
    environment: Mapping[str, str],
    config_file: Path,
) -> Iterator[subprocess.Popen[bytes]]:
    """Start and reap exactly one gate-owned foreground service group."""
    log_file = config_file.parent / "service.log"
    log_handle = log_file.open("wb")
    process = subprocess.Popen(
        service_command(config_file),
        cwd=ROOT,
        env=dict(environment),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        log_handle.close()
        if _process_group_exists(process.pid):
            raise BrowserGateError(
                f"EPG service process group {process.pid} remains after cleanup"
            )


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        try:
            connection.connect((DEFAULT_HOST, port))
        except OSError:
            return False
    return True


def wait_for_service(
    process: subprocess.Popen[bytes],
    service_port: int,
    *,
    timeout: float = 30,
) -> None:
    """Wait for the owned service and reject an early process exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BrowserGateError(
                f"EPG service exited before listening: {process.returncode}"
            )
        if _port_is_open(service_port):
            return
        time.sleep(0.1)
    raise BrowserGateError(f"EPG service did not listen on port {service_port}")


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def owned_redis_server(
    state_root: Path,
    *,
    environment: Mapping[str, str],
) -> Iterator[str]:
    """Start one non-persistent Redis instance and prove cleanup."""
    executable = shutil.which("redis-server", path=environment.get("PATH"))
    if executable is None:
        raise BrowserGateError("The notification gate requires redis-server")
    state_root.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    log_handle = (state_root / "redis.log").open("wb")
    process = subprocess.Popen(
        [
            executable,
            "--bind",
            DEFAULT_HOST,
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--daemonize",
            "no",
            "--dir",
            str(state_root),
            "--pidfile",
            str(state_root / "redis.pid"),
        ],
        cwd=state_root,
        env=dict(environment),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BrowserGateError(
                    f"Redis exited before listening: {process.returncode}"
                )
            if _port_is_open(port):
                break
            time.sleep(0.1)
        else:
            raise BrowserGateError("Redis did not become ready")
        yield f"redis://{DEFAULT_HOST}:{port}"
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        log_handle.close()
        if _process_group_exists(process.pid) or _port_is_open(port):
            raise BrowserGateError("Gate-owned Redis was not fully cleaned up")


def run_browser_gate(
    browser: str,
    environment: Mapping[str, str],
    config_file: Path,
    service_port: int,
) -> int:
    """Run the real browser child and require its strict success payload."""
    command = [
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
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=dict(environment),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise BrowserGateError(
            f"EPG {browser} notification gate failed: {completed.stdout[-4000:]}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserGateError("EPG browser child returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or result.get("browser") != browser
        or result.get("consoleErrors") != []
        or result.get("pageErrors") != []
        or result.get("badResponses") != []
    ):
        raise BrowserGateError(f"EPG browser result was not clean: {result}")
    print(completed.stdout, end="")
    return 0


def run_notification_gate(
    browser: str,
    state_root: Path,
    *,
    source_environment: Mapping[str, str] | None = None,
) -> int:
    """Assemble and execute the focused EPG browser lifecycle."""
    environment = minimal_environment(
        os.environ if source_environment is None else source_environment
    )
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
        migrate_gate_database(config_file, state_root)
        if browser == "firefox":
            load_gate_fixture(environment, config_file)
        ensure_gate_admin(environment, config_file)
        with owned_service(environment, config_file) as process:
            wait_for_service(process, service_port)
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
