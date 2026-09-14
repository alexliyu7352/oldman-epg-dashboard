#!/usr/bin/env python3
"""启动真实后台服务并执行浏览器门禁。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.linux_process_tree import ProcessTreeError, ProcessTreeTracker, tracked_popen  # noqa: E402
from scripts.png_evidence import PngEvidenceError, require_png  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17998
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "CHROME_BIN",
        "DISPLAY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "OLDMAN_ADMIN_PASSWORD",
        "OLDMAN_ADMIN_USERNAME",
        "OLDMAN_AUTH_EXPIRY_EVIDENCE_DIR",
        "OLDMAN_CHROME_HEADLESS",
        "OLDMAN_EPG_BROWSER_EVIDENCE_DIR",
        "OLDMAN_EPG_CHILD_SCREENSHOT_DIR",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "XAUTHORITY",
    }
)
DASHBOARD_CHILD_SCREENSHOTS = frozenset(
    {
        "desktop-dashboard",
        "desktop-dashboard-analytics",
        "desktop-dashboard-chart-loading-3g",
        "desktop-channels-list",
        "desktop-channel-form",
        "desktop-channel-names-list",
        "desktop-channel-name-form",
        "desktop-catalog-channels-list",
        "desktop-catalog-channel-form",
        "desktop-catalog-feeds-list",
        "desktop-catalog-feed-form",
        "desktop-upstream-records-list",
        "desktop-logo-assets-list",
        "desktop-match-decisions-list",
        "desktop-users-list",
        "desktop-user-session",
        "desktop-notifications-list",
        "desktop-programmes-list",
        "desktop-programme-form",
        "preloader-critical-first-paint",
        "mobile-dashboard",
        "mobile-dashboard-analytics",
        "mobile-channels-list",
        "mobile-channel-form",
        "mobile-channel-names-list",
        "mobile-channel-name-form",
        "mobile-catalog-channels-list",
        "mobile-catalog-channel-form",
        "mobile-catalog-feeds-list",
        "mobile-catalog-feed-form",
        "mobile-upstream-records-list",
        "mobile-logo-assets-list",
        "mobile-match-decisions-list",
        "mobile-users-list",
        "mobile-user-session",
        "mobile-notifications-list",
        "mobile-programmes-list",
        "mobile-programme-form",
    }
)
TAILWIND_CHILD_ROUTES = (
    "/",
    "/dashboard/analytics",
    "/users",
    "/users/new",
    "/user-session",
    "/catalog-feeds",
    "/catalog-feeds/new",
    "/catalog-channels",
    "/catalog-channels/new",
    "/channel-names",
    "/channel-names/new",
    "/channels-epg",
    "/channels-epg/new",
    "/epg-list",
    "/epg-list/new",
    "/upstream-records",
    "/logo-assets",
    "/match-decisions",
    "/notifications",
)
TAILWIND_CHILD_SCREENSHOTS = frozenset(
    {
        "route:/login:desktop",
        "tailwind-dashboard-chart-loading-3g",
        "tailwind-desktop-dashboard",
        "tailwind-login",
        "tailwind-mobile-dashboard",
        "tailwind-modal",
        "tailwind-users-interactions",
        "tailwind-route-screenshot-dir",
        *(
            f"route:{path}:{viewport}"
            for path in TAILWIND_CHILD_ROUTES
            for viewport in ("desktop", "wide", "medium", "mobile")
        ),
    }
)
EXAMPLES_CHILD_SCREENSHOTS = frozenset(
    {
        "examples-desktop-tables",
        "examples-desktop-forms",
        "examples-desktop-ui",
        "examples-dropdowns-overlays",
        "examples-gallery",
        "examples-medium-ui",
        "examples-mobile-ui",
        "examples-mobile-plugins",
    }
)
REQUIRED_CHILD_SCREENSHOTS = {
    "verify-dashboard-browser.py": DASHBOARD_CHILD_SCREENSHOTS,
    "verify-examples-browser.py": EXAMPLES_CHILD_SCREENSHOTS,
    "verify-tailwind-browser.py": TAILWIND_CHILD_SCREENSHOTS,
}
CHILD_SCREENSHOT_ALIASES = {
    "verify-dashboard-browser.py": ("desktop-dashboard", "mobile-dashboard"),
    "verify-examples-browser.py": ("examples-desktop-tables", "examples-mobile-plugins"),
    "verify-tailwind-browser.py": ("tailwind-desktop-dashboard", "tailwind-mobile-dashboard"),
}


class BrowserGateError(RuntimeError):
    """浏览器门禁包装脚本执行失败。"""


def stop_output_confirms_pid(output: str, pid: int) -> bool:
    """Require the CLI to report the exact PID after its os.kill call succeeded."""
    ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    return str(pid) in {ansi.sub("", line).strip() for line in output.splitlines()}


def main() -> int:
    """准备管理员账号、启动服务、执行浏览器门禁并清理服务进程。"""
    with tempfile.TemporaryDirectory(prefix="oldman-epg-browser-gate-") as temp_dir:
        state_root = Path(temp_dir)
        env = build_env(state_root=state_root)
        host, port = managed_server_address(env)
        if port_is_open(host, port):
            raise BrowserGateError(f"浏览器门禁拒绝接管已有服务：{host}:{port}")
        if env.get("OLDMAN_BROWSER_GATE_SEEDED") == "1":
            migrate_gate_database(env, state_root)
            load_gate_fixture(env)
            ensure_default_admin(env)
            with start_service(env) as (service_process, process_tree):
                try:
                    wait_for_port(host, port, process=service_process)
                    return run_browser_gate(env)
                finally:
                    stop_service(env, service_process, process_tree)
        with owned_redis_server(state_root / "redis", environment=env) as redis_url:
            env["OLDMAN_GATE_STATIC_ROOT"] = str(state_root / "static")
            prepare_gate_settings(env, state_root, redis_url=redis_url)
            prepare_gate_static(env, state_root)
            migrate_gate_database(env, state_root)
            load_gate_fixture(env)
            ensure_default_admin(env)
            with start_service(env) as (service_process, process_tree):
                try:
                    wait_for_port(host, port, process=service_process)
                    return run_browser_gate(env)
                finally:
                    stop_service(env, service_process, process_tree)


def find_free_port() -> int:
    """选择一个当前未占用的独立 loopback 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((DEFAULT_HOST, 0))
        return int(listener.getsockname()[1])


def prepare_gate_static(env: dict[str, str], state_root: Path) -> None:
    """通过正式 CLI 收集项目、框架和已安装 App 的静态资源。"""
    if env.get("OLDMAN_BROWSER_GATE_SEEDED") == "1":
        return
    subprocess.run(
        [
            sys.executable,
            "-m",
            "oldman.cli",
            "web",
            "static",
            "collect",
            "--config",
            env["OLDMAN_GATE_CONFIG_FILE"],
            "--clear",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def prepare_gate_settings(
    env: dict[str, str],
    state_root: Path,
    *,
    redis_url: str,
) -> Path:
    """Write the complete isolated Web YAML consumed by every gate process."""
    config_file = Path(env["OLDMAN_GATE_CONFIG_FILE"])
    payload = YAML(typ="safe", pure=True).load(
        (ROOT / "data" / "web_settings.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload["database"]["url"] = (
        f"sqlite+aiosqlite:///{state_root / 'dashboard.sqlite3'}"
    )
    payload["logging"]["dir"] = str(state_root / "logs")
    # This UI gate owns Redis, not NATS; distributed tasks have a separate real-service check.
    payload["taskiq"]["enabled"] = False
    payload["nats_bus"]["enabled"] = False
    payload["process"]["pid_dir"] = str(state_root / "pids")
    payload["redis"]["SESSION"]["redis_url"] = redis_url
    payload["redis"]["CACHE"]["redis_url"] = redis_url
    payload["redis"]["SSE"]["redis_url"] = redis_url
    payload["web"]["listen_host"] = DEFAULT_HOST
    payload["web"]["listen_port"] = int(
        urllib.parse.urlparse(env["OLDMAN_DASHBOARD_URL"]).port
        or DEFAULT_PORT
    )
    payload["web"]["security"]["secret_key"] = secrets.token_urlsafe(48)
    payload["web"]["security"]["fingerprint"]["aes_secret_key"] = (
        base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    )
    payload["web"]["sse"]["heartbeat_interval"] = 0.5
    payload["web"]["sse"]["session_check_interval"] = 0.5
    payload["web"]["static"]["root"] = env["OLDMAN_GATE_STATIC_ROOT"]
    payload["storages"]["default"]["options"]["location"] = str(state_root / "media")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with config_file.open("w", encoding="utf-8") as file:
        yaml.dump(payload, file)
    return config_file


def migrate_gate_database(env: dict[str, str], state_root: Path) -> None:
    """Apply the project's reviewed migrations to the isolated gate database."""
    from oldman.db.migrations.commands import migrate
    from oldman.db.migrations.project import load_migration_project

    project_root = state_root / "migration-project"
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "services").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "pyproject.toml", project_root / "pyproject.toml")
    shutil.copy2(
        Path(env["OLDMAN_GATE_CONFIG_FILE"]),
        project_root / "data" / "web_settings.yaml",
    )
    (project_root / "services" / "web.py").write_text(
        "from oldman.runtime.web import WebApplication\n\n"
        "class WebService(WebApplication):\n"
        "    pass\n",
        encoding="utf-8",
    )

    class GateMigrationInteraction:
        """Confirm only the known empty gate database first-use state."""

        is_interactive = False

        def choose(self, prompt: str, choices: tuple[str, ...]) -> str:
            if "internal migration state" in prompt and "first use" in choices:
                return "first use"
            raise BrowserGateError(f"浏览器门禁遇到未计划的迁移选择：{prompt}")

        def confirm(self, prompt: str, *, default: bool = False) -> bool:
            del default
            raise BrowserGateError(f"浏览器门禁遇到未计划的迁移确认：{prompt}")

        def text(self, prompt: str, *, default: str) -> str:
            del default
            raise BrowserGateError(f"浏览器门禁不应生成迁移：{prompt}")

    migrate(
        load_migration_project(project_root),
        GateMigrationInteraction(),
    )


@contextmanager
def owned_redis_server(
    state_root: Path,
    *,
    environment: Mapping[str, str],
) -> Iterator[str]:
    """启动本门禁独占的无持久化 Redis，并在退出时证明进程和端口均已回收。"""
    executable = shutil.which("redis-server", path=environment.get("PATH"))
    if executable is None:
        raise BrowserGateError("真实浏览器门禁需要 redis-server")

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
        wait_for_port(DEFAULT_HOST, port, process=process, timeout=10)
        yield f"redis://{DEFAULT_HOST}:{port}/0"
    finally:
        cleanup_errors: list[str] = []
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
        if process.returncode not in {0, -signal.SIGTERM}:
            cleanup_errors.append(f"Redis 返回非预期退出码 {process.returncode}")
        if process_group_exists(process.pid):
            cleanup_errors.append(f"Redis 进程组 {process.pid} 仍有残留")
        try:
            wait_for_port_to_close(DEFAULT_HOST, port, timeout=2)
        except BrowserGateError as exc:
            cleanup_errors.append(str(exc))
        log_handle.close()
        if cleanup_errors:
            raise BrowserGateError("; ".join(cleanup_errors))


def build_env(*, state_root: Path | None = None, source: dict[str, str] | None = None) -> dict[str, str]:
    """构造门禁控制变量，不把环境变量当作业务 settings。"""
    caller = os.environ if source is None else source
    seeded = caller.get("OLDMAN_BROWSER_GATE_SEEDED") == "1"
    env = dict(caller) if seeded else {
        name: value for name, value in caller.items() if name in ENVIRONMENT_ALLOWLIST and value
    }
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("PATH", os.defpath)
    env.setdefault("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME)
    env.setdefault("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD)
    if seeded:
        parsed = urllib.parse.urlparse(env.get("OLDMAN_DASHBOARD_URL", ""))
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port is None:
            raise BrowserGateError("父门禁提供的 OLDMAN_DASHBOARD_URL 必须是带端口的 loopback URL")
        port = parsed.port
        config_file = env.get("OLDMAN_GATE_CONFIG_FILE") or env.get(
            "CONFIG_FILE"
        )
        if not config_file:
            raise BrowserGateError("父门禁没有提供隔离 Web settings 文件")
        env["OLDMAN_GATE_CONFIG_FILE"] = config_file
    else:
        port = find_free_port()
        if state_root is not None:
            state_root.mkdir(parents=True, exist_ok=True)
            env["OLDMAN_GATE_CONFIG_FILE"] = str(
                state_root / "web_settings.yaml"
            )
            env["OLDMAN_EPG_CHILD_SCREENSHOT_DIR"] = str(state_root / "browser-screenshots")
    env["OLDMAN_DASHBOARD_URL"] = f"http://{DEFAULT_HOST}:{port}/"
    env["OLDMAN_DEV"] = "0"
    env["PYTHONHASHSEED"] = "0"
    env["TZ"] = "UTC"
    return env


def managed_server_address(env: dict[str, str]) -> tuple[str, int]:
    """从环境变量中解析需要管理的本地服务地址。"""
    parsed = urllib.parse.urlparse(env["OLDMAN_DASHBOARD_URL"])
    host = parsed.hostname or DEFAULT_HOST
    port = int(parsed.port or DEFAULT_PORT)
    return host, port


def ensure_default_admin(env: dict[str, str]) -> None:
    """调用现有脚本幂等创建浏览器门禁需要的管理员账号。"""
    command = [
        sys.executable,
        str(ROOT / "scripts" / "create_admin.py"),
        "--username",
        env["OLDMAN_ADMIN_USERNAME"],
        "--password",
        env["OLDMAN_ADMIN_PASSWORD"],
        "--email",
        "oldman@example.com",
        "--config",
        env["OLDMAN_GATE_CONFIG_FILE"],
    ]
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def load_gate_fixture(env: dict[str, str]) -> None:
    """Load the deterministic examples fixture through the public command lifecycle."""
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
        [sys.executable, "-c", source, env["OLDMAN_GATE_CONFIG_FILE"]],
        cwd=ROOT,
        env=env,
        check=True,
    )


def port_is_open(host: str, port: int) -> bool:
    """判断本地服务端口是否已经可连接。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try:
            sock.connect((host, port))
        except OSError:
            return False
        return True


def start_service(env: dict[str, str]):
    """在 Linux subreaper 生效后以前台子进程启动 Oldman web 服务。"""
    command = service_command(env, "start")
    return tracked_popen(command, cwd=ROOT, env=env, start_new_session=True)


def service_command(env: dict[str, str], action: str) -> list[str]:
    """Bootstrap one explicit test config before invoking a service command."""
    source = (
        "import sys; "
        "from pathlib import Path; "
        "from oldman import bootstrap_service; "
        "from oldman.runtime.discovery import get_service_definition, load_service_class; "
        "bootstrap_service('web', config_file=sys.argv[1]); "
        "definition = get_service_definition('web', Path.cwd()); "
        "service = load_service_class(definition); "
        "result = service.execute_command(sys.argv[2]); "
        "print(result) if result else None"
    )
    return [
        sys.executable,
        "-c",
        source,
        env["OLDMAN_GATE_CONFIG_FILE"],
        action,
    ]


def wait_for_port(
    host: str,
    port: int,
    *,
    process: subprocess.Popen[bytes] | None = None,
    timeout: float = 30.0,
) -> None:
    """等待服务端口打开，超时则终止验收。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise BrowserGateError(f"后台服务在监听前退出：{process.returncode}")
        if port_is_open(host, port):
            return
        time.sleep(0.2)
    raise BrowserGateError(f"等待后台服务启动超时：{host}:{port}")


def wait_for_port_to_close(host: str, port: int, *, timeout: float = 10.0) -> None:
    """等待本门禁拥有的服务释放端口。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_open(host, port):
            return
        time.sleep(0.2)
    raise BrowserGateError(f"等待已有后台服务停止超时：{host}:{port}")


def process_group_exists(process_group: int) -> bool:
    """判断门禁拥有的 POSIX 进程组中是否仍有进程。"""
    if os.name != "posix":  # pragma: no cover - 当前发布范围为 Linux
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_process_group_exit(process_group: int, *, timeout: float = 2.0) -> bool:
    """等待门禁拥有的整个进程组退出。"""
    deadline = time.monotonic() + timeout
    while process_group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not process_group_exists(process_group)


def browser_gate_scripts() -> list[Path]:
    """返回当前 Tailwind/Oldman 协议下的总浏览器门禁脚本。"""
    return [
        ROOT / "scripts" / "verify-dashboard-browser.py",
        ROOT / "scripts" / "verify-examples-browser.py",
        ROOT / "scripts" / "verify-tailwind-browser.py",
    ]


def require_browser_child_output(
    script: Path,
    stdout: str,
    *,
    evidence_root: Path | None,
) -> None:
    """Require complete JSON plus retained real screenshots from each browser child."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BrowserGateError(f"{script.name} 返回无效 JSON: {exc}") from exc
    required_keys = {
        "ok",
        "consoleErrors",
        "pageErrors",
        "badResponses",
        "desktopScreenshot",
        "mobileScreenshot",
        "screenshots",
    }
    if not isinstance(payload, dict) or set(payload) != required_keys:
        raise BrowserGateError(f"{script.name} 浏览器结果字段不完整")
    if (
        payload.get("ok") is not True
        or payload.get("consoleErrors") != []
        or payload.get("pageErrors") != []
        or payload.get("badResponses") != []
    ):
        raise BrowserGateError(f"{script.name} 浏览器结果不是无错误成功结果")
    screenshots = payload.get("screenshots")
    if not isinstance(screenshots, dict) or not screenshots:
        raise BrowserGateError(f"{script.name} 没有交互截图集合")
    required_screenshots = REQUIRED_CHILD_SCREENSHOTS.get(script.name)
    if required_screenshots is None or set(screenshots) != required_screenshots:
        raise BrowserGateError(f"{script.name} 缺少冻结的交互截图集合")
    required_paths = [payload.get("desktopScreenshot"), payload.get("mobileScreenshot")]
    if any(not isinstance(path, str) or not path for path in required_paths):
        raise BrowserGateError(f"{script.name} 缺少桌面或移动截图")
    screenshot_paths: set[Path] = set()
    screenshot_sources: dict[str, Path] = {}
    screenshot_directories: list[Path] = []
    for label, raw_path in {
        "desktopScreenshot": required_paths[0],
        "mobileScreenshot": required_paths[1],
        **{f"screenshots.{name}": path for name, path in screenshots.items()},
    }.items():
        if not isinstance(raw_path, str) or not raw_path:
            raise BrowserGateError(f"{script.name} 截图路径无效: {label}")
        path = Path(raw_path).expanduser().resolve()
        if label.endswith("-dir"):
            if not path.is_dir() or path.is_symlink():
                raise BrowserGateError(f"{script.name} 截图目录无效: {label}")
            screenshot_directories.append(path)
            continue
        try:
            require_png(path)
        except PngEvidenceError as exc:
            raise BrowserGateError(f"{script.name} PNG 截图无效: {label}: {exc}") from exc
        screenshot_paths.add(path)
        if label.startswith("screenshots."):
            screenshot_sources[label.removeprefix("screenshots.")] = path
    regular_pngs = sorted(screenshot_paths)
    identities = {require_png(path).sha256 for path in regular_pngs}
    expected_png_count = len(required_screenshots) - sum(name.endswith("-dir") for name in required_screenshots)
    desktop_alias, mobile_alias = CHILD_SCREENSHOT_ALIASES[script.name]
    if (
        len(regular_pngs) != expected_png_count
        or len(screenshot_sources) != expected_png_count
        or len(identities) < 4
        or Path(str(required_paths[0])).resolve() != screenshot_sources[desktop_alias]
        or Path(str(required_paths[1])).resolve() != screenshot_sources[mobile_alias]
    ):
        raise BrowserGateError(f"{script.name} 截图没有覆盖至少四个不同的真实状态")
    if evidence_root is None:
        return
    destination = evidence_root / script.stem
    destination.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, object]] = []
    records_by_label: dict[str, dict[str, object]] = {}
    for label, source in sorted(screenshot_sources.items()):
        artifact_name = f"{urllib.parse.quote(label, safe='')}.png"
        target = destination / "screenshots" / artifact_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        record = {
            "bytes": target.stat().st_size,
            "path": target.relative_to(destination).as_posix(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        artifacts.append(record)
        records_by_label[label] = record
    artifacts.sort(key=lambda record: str(record["path"]))
    captures = {
        "desktopScreenshot": records_by_label[desktop_alias],
        "mobileScreenshot": records_by_label[mobile_alias],
        "screenshots": records_by_label,
    }
    retained_browser_result = dict(payload)
    retained_browser_result["desktopScreenshot"] = str(
        destination / str(records_by_label[desktop_alias]["path"])
    )
    retained_browser_result["mobileScreenshot"] = str(
        destination / str(records_by_label[mobile_alias]["path"])
    )
    retained_browser_result["screenshots"] = {
        label: (
            str(destination / "screenshots")
            if label.endswith("-dir")
            else str(destination / str(records_by_label[label]["path"]))
        )
        for label in required_screenshots
    }
    (destination / "result.json").write_text(
        json.dumps(
            {
                "artifacts": artifacts,
                "browserResult": retained_browser_result,
                "captures": captures,
                "child": script.name,
                "childOutputSha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for source in regular_pngs:
        source.unlink(missing_ok=True)
    for directory in sorted(screenshot_directories, reverse=True):
        shutil.rmtree(directory, ignore_errors=True)


def run_browser_gates(env: dict[str, str]) -> int:
    """执行当前项目的浏览器总门禁。"""
    configured_evidence = env.get("OLDMAN_EPG_BROWSER_EVIDENCE_DIR")
    evidence_root = Path(configured_evidence).expanduser().resolve() if configured_evidence else None
    for script in browser_gate_scripts():
        command = [sys.executable, str(script)]
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False, capture_output=True, text=True)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            return completed.returncode
        require_browser_child_output(script, completed.stdout, evidence_root=evidence_root)
    return 0


def run_browser_gate(env: dict[str, str]) -> int:
    """执行当前项目的浏览器门禁。"""
    return run_browser_gates(env)


def stop_service(
    env: dict[str, str],
    service_process: subprocess.Popen[bytes] | None,
    process_tree: ProcessTreeTracker,
) -> None:
    """通过公开 stop 命令停止服务，并证明完整 owned process tree 已消失。"""
    if service_process is None:
        raise BrowserGateError("拒绝在没有 owned process 的情况下执行 web stop")
    errors: list[str] = []
    leader_was_running = service_process.poll() is None
    process_tree.remember()
    if not leader_was_running:
        errors.append(f"服务主进程在门禁发起停止前退出，退出码 {service_process.returncode}")
    command = service_command(env, "stop")
    leader_suspended = False
    if leader_was_running:
        try:
            process_tree.signal_leader(signal.SIGSTOP, require_live_leader=True)
            leader_suspended = process_tree.wait_for_leader_state(frozenset({"T", "t"}), 2)
            if not leader_suspended:
                errors.append("owned leader 未在 stop 命令前进入暂停状态")
        except ProcessTreeError as exc:
            errors.append(str(exc))
        if leader_suspended:
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except Exception as exc:
                errors.append(f"web stop 执行失败: {type(exc).__name__}: {exc}")
            else:
                if completed.returncode != 0:
                    errors.append(
                        f"web stop 返回 {completed.returncode}: "
                        f"{(completed.stdout + completed.stderr).strip()[-2000:]}"
                    )
                elif not stop_output_confirms_pid(completed.stdout, service_process.pid):
                    errors.append("web stop 未返回 owned leader PID，无法证明停止信号已送达")
            finally:
                try:
                    process_tree.signal_leader(signal.SIGCONT, require_live_leader=True)
                except ProcessTreeError as exc:
                    errors.append(f"stop 命令后无法恢复 owned leader: {exc}")
    survivors = process_tree.wait_for_exit(service_process, 8 if leader_was_running else 0)
    if survivors:
        errors.append(f"owned service process tree 在优雅停止后仍有残留: {survivors}")
        try:
            process_tree.terminate(
                service_process,
                require_live_leader=False,
                term_timeout=5,
                kill_timeout=5,
            )
        except ProcessTreeError as exc:
            errors.append(str(exc))
    if isinstance(service_process.returncode, int) and service_process.returncode != 0:
        errors.append(f"服务主进程返回非零退出码 {service_process.returncode}")
    host, port = managed_server_address(env)
    try:
        wait_for_port_to_close(host, port, timeout=2)
    except BrowserGateError as exc:
        errors.append(str(exc))
    if errors:
        raise BrowserGateError("; ".join(errors))


if __name__ == "__main__":
    raise SystemExit(main())
