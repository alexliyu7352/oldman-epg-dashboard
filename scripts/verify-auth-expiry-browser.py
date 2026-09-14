#!/usr/bin/env python3
"""验证 AJAX 会话过期时浏览器会跳转登录页。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

try:
    from scripts.png_evidence import require_png
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.png_evidence import require_png

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 17998
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
WRAPPER_PATH = ROOT / "scripts" / "verify-dashboard-browser-with-server.py"
TAILWIND_GATE_PATH = ROOT / "scripts" / "verify-tailwind-browser.py"


def load_module(path: Path, name: str):
    """从带连字符的脚本路径加载模块。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WRAPPER = load_module(WRAPPER_PATH, "oldman_browser_gate_wrapper")
TAILWIND_GATE = load_module(TAILWIND_GATE_PATH, "oldman_tailwind_gate")

CDPClient = TAILWIND_GATE.CDPClient
VerificationResult = TAILWIND_GATE.VerificationResult
configure_viewport = TAILWIND_GATE.configure_viewport
create_page_websocket = TAILWIND_GATE.create_page_websocket
find_free_port = TAILWIND_GATE.find_free_port
launch_chrome = TAILWIND_GATE.launch_chrome
login = TAILWIND_GATE.login
navigate = TAILWIND_GATE.navigate
save_screenshot = TAILWIND_GATE.save_screenshot
wait_for_chrome_devtools = TAILWIND_GATE.wait_for_chrome_devtools
wait_for_oldman_ready = TAILWIND_GATE.wait_for_oldman_ready


def main() -> int:
    """启动服务并执行会话过期浏览器门禁。"""
    with tempfile.TemporaryDirectory(prefix="oldman-auth-expiry-gate-") as temp_dir:
        env = build_env(state_root=Path(temp_dir))
        host, port = WRAPPER.managed_server_address(env)

        if WRAPPER.port_is_open(host, port):
            raise RuntimeError(f"auth-expiry 门禁拒绝接管已有服务：{host}:{port}")
        WRAPPER.ensure_default_admin(env)
        with WRAPPER.start_service(env) as (service_process, process_tree):
            try:
                WRAPPER.wait_for_port(host, port, process=service_process)
                return run_auth_expiry_browser_gate(env)
            finally:
                WRAPPER.stop_service(env, service_process, process_tree)


def build_env(*, state_root: Path | None = None) -> dict[str, str]:
    """构造服务和浏览器共享环境。"""
    env = WRAPPER.build_env(state_root=state_root)
    env.setdefault("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME)
    env.setdefault("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD)
    if evidence_dir := os.environ.get("OLDMAN_AUTH_EXPIRY_EVIDENCE_DIR"):
        env["OLDMAN_AUTH_EXPIRY_EVIDENCE_DIR"] = evidence_dir
    return env


def run_auth_expiry_browser_gate(env: dict[str, str]) -> int:
    """执行浏览器验证并输出 JSON 结果。"""
    configured_evidence = env.get("OLDMAN_AUTH_EXPIRY_EVIDENCE_DIR")
    evidence_dir = (
        Path(configured_evidence).expanduser().resolve()
        if configured_evidence
        else Path(tempfile.mkdtemp(prefix="oldman-auth-expiry-evidence-"))
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot = evidence_dir / "auth-expiry-login.png"
    result = VerificationResult(desktopScreenshot=str(screenshot), mobileScreenshot="")
    base_url = env.get("OLDMAN_DASHBOARD_URL", f"http://127.0.0.1:{DEFAULT_PORT}/")
    proof = verify_auth_expiry(
        base_url,
        env["OLDMAN_ADMIN_USERNAME"],
        env["OLDMAN_ADMIN_PASSWORD"],
        result,
        screenshot=screenshot,
    )
    result.ok = not result.consoleErrors and not result.pageErrors and not result.badResponses
    payload = result.as_json()
    payload["statusMatrix"] = {
        "ajaxRequested": proof.get("ajaxRequested") is True,
        "loginForm": proof.get("hasLoginForm") is True,
        "loginRedirect": proof.get("path") == "/login",
        "noRendered403": proof.get("rendered403") is False,
    }
    payload["ok"] = result.ok and all(payload["statusMatrix"].values())
    screenshot_evidence = require_png(screenshot)
    payload["artifacts"] = (
        [
            {
                "bytes": screenshot_evidence.bytes,
                "path": screenshot.name,
                "sha256": screenshot_evidence.sha256,
            }
        ]
        if screenshot.is_file()
        else []
    )
    (evidence_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] and len(payload["artifacts"]) == 1 else 1


def verify_auth_expiry(
    base_url: str,
    username: str,
    password: str,
    result: VerificationResult,
    *,
    screenshot: Path,
) -> dict[str, object]:
    """登录后删除 session cookie，验证表格 AJAX 触发登录跳转。"""
    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="oldman-auth-expiry-chrome-")
    chrome = launch_chrome(port, user_data_dir)
    client: CDPClient | None = None
    try:
        wait_for_chrome_devtools(chrome, port)
        client = CDPClient(create_page_websocket(port), result)
        assert client is not None
        client.command("Page.enable")
        client.command("Runtime.enable")
        client.command("Network.enable")
        client.command("Log.enable")
        seen_paths = record_request_paths(client)
        configure_viewport(client, 1440, 1000, mobile=False)

        login(client, base_url, username, password)
        navigate(client, urllib.parse.urljoin(base_url, "/channels-epg"))
        wait_for_oldman_ready(client)
        clear_session_cookie(client, base_url)
        trigger_channels_table_reload(client)
        state = wait_for_login_redirect(client, seen_paths)
        failures = failure_messages(state.get("failures"))
        result.pageErrors.extend(f"auth expiry: {failure}" for failure in failures)
        save_screenshot(client, str(screenshot))
        return {
            **state,
            "ajaxRequested": "/channels-epg/table" in seen_paths,
            "rendered403": "Request failed with status code 403"
            in str(client.evaluate("document.body?.innerText || ''")),
        }
    finally:
        if client is not None:
            client.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()
            chrome.wait(timeout=5)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def clear_session_cookie(client: CDPClient, base_url: str) -> None:
    """删除浏览器中的后台 session cookie。"""
    client.command("Network.deleteCookies", {"name": "oldman_session_id", "url": urllib.parse.urljoin(base_url, "/")})


def trigger_channels_table_reload(client: CDPClient) -> None:
    """触发 ChannelsEpgTable 重新请求 /channels-epg/table。"""
    client.evaluate(
        """
        (() => {
          performance.clearResourceTimings();
          const table = document.querySelector("[data-om-component='table']");
          if (table) {
            table.dispatchEvent(new CustomEvent("om:table:reload", { bubbles: true }));
          }
        })()
        """
    )


def record_request_paths(client: CDPClient) -> set[str]:
    """记录当前页面发出的网络请求 path。"""
    seen_paths: set[str] = set()
    original_handle_event = client._handle_event

    def handle_event(message: dict[str, object]) -> None:
        if message.get("method") == "Network.requestWillBeSent":
            params = message.get("params", {})
            if isinstance(params, dict):
                request = params.get("request", {})
                if isinstance(request, dict):
                    url = str(request.get("url") or "")
                    if url:
                        seen_paths.add(urllib.parse.urlparse(url).path)
        original_handle_event(message)

    client._handle_event = handle_event
    return seen_paths


def wait_for_login_redirect(client: CDPClient, seen_paths: set[str], *, timeout: float = 8.0) -> dict[str, object]:
    """等待顶层页面跳转到登录页并返回检查结果。"""
    deadline = time.monotonic() + timeout
    last_state: dict[str, object] = {}
    while time.monotonic() < deadline:
        state = client.evaluate(
            """
            (() => {
              const text = document.body?.innerText || "";
              const failures = [];
              if (text.includes("Request failed with status code 403")) failures.push("page rendered 403 table error");
              return {
                failures,
                path: location.pathname,
                hasLoginForm: Boolean(document.querySelector('form[action="/login"]')),
                ready: document.readyState
              };
            })()
            """
        )
        last_state = state
        if state.get("path") == "/login" and state.get("hasLoginForm"):
            if "/channels-epg/table" not in seen_paths:
                failures = failure_messages(state.get("failures"))
                failures.append("missing /channels-epg/table AJAX request")
                state["failures"] = failures
            return state
        time.sleep(0.2)
    failures = failure_messages(last_state.get("failures"))
    if "/channels-epg/table" not in seen_paths:
        failures.append("missing /channels-epg/table AJAX request")
    failures.append('location.pathname === "/login" was not reached after session expiry')
    last_state["failures"] = failures
    return last_state


def failure_messages(value: object) -> list[str]:
    """Normalize a browser assertion's failure list."""
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
