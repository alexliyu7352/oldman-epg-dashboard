#!/usr/bin/env python3
"""启动真实 Vite 服务并验证 EPG dashboard 模板预览。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

try:
    from scripts.linux_process_tree import ProcessTreeError, ProcessTreeTracker, tracked_popen
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.linux_process_tree import ProcessTreeError, ProcessTreeTracker, tracked_popen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_PATH = "/templates/pages/dashboard.html"


class PreviewGateError(RuntimeError):
    """模板预览门禁无法完成。"""


def oldman_template_dir() -> Path:
    """从当前 Python 导入目标解析源码树或已安装 wheel 的模板目录。"""
    import oldman

    package_file = Path(oldman.__file__ or "").resolve()
    template_dir = package_file.parent / "web" / "templates"
    if not (template_dir / "oldman" / "dashboard" / "base.html").is_file():
        raise PreviewGateError(f"Oldman dashboard templates were not found: {template_dir}")
    return template_dir


def find_free_port() -> int:
    """选择当前本机未占用端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def start_vite(port: int, static_root: Path):
    """使用与公开 preview 命令相同的 Vite 入口启动隔离服务。"""
    env = os.environ.copy()
    env["OLDMAN_COLLECTED_STATIC_DIR"] = str(static_root)
    env["OLDMAN_PYTHON_TEMPLATE_DIR"] = str(oldman_template_dir())
    command = [
        "pnpm",
        "--dir",
        "frontend",
        "preview:templates",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    return tracked_popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def fetch_dashboard(process: subprocess.Popen[str], port: int, *, timeout: float = 30.0) -> str:
    """等待 Vite 就绪并取得 dashboard 预览 HTML。"""
    url = f"http://127.0.0.1:{port}{PREVIEW_PATH}"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise PreviewGateError(f"Vite exited before serving the preview ({process.returncode}):\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    raise PreviewGateError(f"Unexpected preview status: {response.status}")
                return response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.2)
    raise PreviewGateError(f"Timed out waiting for {url}: {last_error}")


def verify_html(html: str) -> None:
    """确认返回的是渲染完成的真实 dashboard 模板。"""
    required_markers = (
        '<html lang="en"',
        'data-om-component="dashboard-overview"',
        'id="programme-trend-chart"',
        'data-om-chart-src="/dashboard/charts/programme-trend"',
        "Oldman News",
        "Administrator",
        '<script type="module" src="/src/main.ts"></script>',
    )
    missing = [marker for marker in required_markers if marker not in html]
    if missing:
        raise PreviewGateError(f"Preview HTML is missing rendered markers: {missing}")
    if "{%" in html or "{{" in html:
        raise PreviewGateError("Preview HTML still contains unrendered Nunjucks syntax")


def verify_framework_flag(port: int) -> None:
    """确认 Vite 预览会从同一个 Python 包提供共享旗帜资源。"""
    url = (
        f"http://127.0.0.1:{port}"
        "/static/oldman/images/flags/cn.svg"
    )
    with urllib.request.urlopen(url, timeout=2) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()
    if response.status != 200:
        raise PreviewGateError(
            f"Unexpected framework flag status: {response.status}"
        )
    if content_type != "image/svg+xml" or b'id="flag-icons-cn"' not in payload:
        raise PreviewGateError(
            "Vite preview did not serve the packaged framework flag"
        )


def port_is_open(port: int) -> bool:
    """判断本门禁的 loopback 端口是否仍在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.2)
        return client.connect_ex(("127.0.0.1", port)) == 0


def process_group_exists(process_group: int) -> bool:
    """判断本门禁拥有的 POSIX 进程组是否仍有成员。"""
    if os.name != "posix":  # pragma: no cover - 当前发布范围为 Linux
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    """等待一个清理条件变为真。"""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return bool(predicate())


def stop_process(process: subprocess.Popen[str], process_tree: ProcessTreeTracker, port: int) -> None:
    """关闭并验证本次门禁拥有的完整 Vite 进程树。"""
    errors: list[str] = []
    already_exited = process.poll() is not None
    if already_exited:
        errors.append(f"Vite leader exited before gate-initiated shutdown with return code {process.returncode}")
    try:
        process_tree.terminate(
            process,
            require_live_leader=not already_exited,
            term_timeout=8,
            kill_timeout=5,
        )
    except ProcessTreeError as exc:
        errors.append(str(exc))
    returncode = process.returncode
    acceptable_returncodes = {0} if already_exited else {0, -signal.SIGTERM}
    if isinstance(returncode, int) and returncode not in acceptable_returncodes:
        errors.append(f"Vite leader returned unexpected exit code {returncode}")
    if not wait_until(lambda: not port_is_open(port)):
        errors.append(f"owned Vite port 127.0.0.1:{port} remains open after shutdown")
    if errors:
        raise PreviewGateError("; ".join(errors))


def main() -> int:
    """运行真实 HTTP 预览门禁。"""
    from oldman.web.staticfiles import collect_project_static

    port = find_free_port()
    with tempfile.TemporaryDirectory(
        prefix="oldman-epg-preview-static-"
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        static_source = temporary_root / "source"
        static_root = temporary_root / "public"
        static_source.mkdir()
        collect_project_static(
            project_directory=static_source,
            destination=static_root,
            clear=True,
        )
        with start_vite(port, static_root) as (process, process_tree):
            try:
                verify_html(fetch_dashboard(process, port))
                verify_framework_flag(port)
            finally:
                stop_process(process, process_tree, port)
    print(f"EPG dashboard Vite template preview passed: http://127.0.0.1:{port}{PREVIEW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
