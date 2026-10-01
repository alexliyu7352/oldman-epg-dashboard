#!/usr/bin/env python3
"""验证找回密码的完整流程在真实浏览器里走得通：申请、收信、设新密码、用新密码登录。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
from email import message_from_bytes, policy
from pathlib import Path

from oldman.testing import require_png

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 17997
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
#: ensure_gate_admin 给门禁管理员的邮箱。
ADMIN_EMAIL = "oldman@example.com"
NEW_PASSWORD = "ResetGate2026"
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


def main() -> int:
    """启动服务并执行找回密码浏览器门禁。"""
    with tempfile.TemporaryDirectory(prefix="oldman-password-reset-gate-") as temp_dir:
        state_root = Path(temp_dir)
        env = WRAPPER.build_env(state_root=state_root)
        env.setdefault("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME)
        env.setdefault("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD)
        # 与主门禁同一套准备步骤；门禁设置把邮件写成文件（mail_dir），链接的域名就是门禁服务的地址。
        with WRAPPER.gate_service(env, state_root):
            return run_password_reset_gate(env, WRAPPER.mail_dir(state_root))


def run_password_reset_gate(env: dict[str, str], mail_dir: Path) -> int:
    """执行浏览器验证并输出 JSON 结果。"""
    configured_evidence = os.environ.get("OLDMAN_PASSWORD_RESET_EVIDENCE_DIR")
    evidence_dir = (
        Path(configured_evidence).expanduser().resolve()
        if configured_evidence
        else Path(tempfile.mkdtemp(prefix="oldman-password-reset-evidence-"))
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot = evidence_dir / "password-reset-done.png"
    result = VerificationResult(desktopScreenshot=str(screenshot), mobileScreenshot="")
    base_url = env.get("OLDMAN_DASHBOARD_URL", f"http://127.0.0.1:{DEFAULT_PORT}/")
    proof = verify_password_reset(base_url, env["OLDMAN_ADMIN_USERNAME"], mail_dir, result, screenshot=screenshot)
    result.ok = not result.consoleErrors and not result.pageErrors and not result.badResponses
    payload = result.as_json()
    payload["statusMatrix"] = {
        "requestAccepted": proof.get("afterRequest") == "/password-reset/sent",
        "mailLinkOnGateAddress": proof.get("linkOnGateAddress") is True,
        "newPasswordSet": proof.get("afterConfirm") == "/password-reset/done",
        "newPasswordLogsIn": proof.get("afterLogin") not in (None, "/login"),
    }
    payload["ok"] = result.ok and all(payload["statusMatrix"].values())
    screenshot_evidence = require_png(screenshot)
    payload["artifacts"] = (
        [{"bytes": screenshot_evidence.bytes, "path": screenshot.name, "sha256": screenshot_evidence.sha256}]
        if screenshot.is_file()
        else []
    )
    (evidence_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] and len(payload["artifacts"]) == 1 else 1


def verify_password_reset(
    base_url: str,
    username: str,
    mail_dir: Path,
    result: VerificationResult,
    *,
    screenshot: Path,
) -> dict[str, object]:
    """未登录申请重置、打开邮件里的链接设新密码，再用新密码登录。

    浏览器按页面的 Referrer-Policy 决定表单 POST 带什么 Origin，所以这两步必须在真实浏览器里点提交，
    测试客户端发什么头都由测试决定，证明不了这一点。
    """
    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="oldman-password-reset-chrome-")
    chrome = launch_chrome(port, user_data_dir)
    client: CDPClient | None = None
    proof: dict[str, object] = {}
    try:
        wait_for_chrome_devtools(chrome, port)
        client = CDPClient(create_page_websocket(port), result)
        assert client is not None
        for domain in ("Page", "Runtime", "Network", "Log"):
            client.command(f"{domain}.enable")
        configure_viewport(client, 1440, 1000, mobile=False)

        navigate(client, urllib.parse.urljoin(base_url, "/password-reset"))
        proof["afterRequest"] = submit_form(client, {"email": ADMIN_EMAIL})
        link = reset_link(mail_dir)
        proof["linkOnGateAddress"] = link.startswith(urllib.parse.urljoin(base_url, "/password-reset/"))
        navigate(client, link)
        proof["afterConfirm"] = submit_form(client, {"password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        save_screenshot(client, str(screenshot))
        login(client, base_url, username, NEW_PASSWORD)
        proof["afterLogin"] = str(client.evaluate("location.pathname"))
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
    return proof


def submit_form(client: CDPClient, fields: dict[str, str]) -> str:
    """填写页面上的 POST 表单，像人一样点它自己的提交按钮，返回落到的路径。"""
    client.load_seen = False
    submitted = client.evaluate(
        "(() => { const values = "
        + json.dumps(fields)
        + """;
          const form = document.querySelector('form[method="post"]');
          const button = form?.querySelector('button[type="submit"]');
          if (!form || !button) return false;
          for (const [name, value] of Object.entries(values)) {
            const input = form.querySelector(`[name="${name}"]`);
            if (!input) return false;
            input.value = value;
          }
          button.click();
          return true;
        })()"""
    )
    if submitted is not True:
        raise RuntimeError(f"表单无法提交：{sorted(fields)}")
    client.wait_for_load()
    client.pump(0.5)
    return str(client.evaluate("location.pathname"))


def reset_link(mail_dir: Path, *, timeout: float = 10.0) -> str:
    """读出唯一一封重置邮件里的链接；邮件由后台任务发送，提交后稍等。"""
    deadline = time.monotonic() + timeout
    while not any(mail_dir.glob("*.eml")) and time.monotonic() < deadline:
        time.sleep(0.2)
    messages = sorted(mail_dir.glob("*.eml"))
    if len(messages) != 1:
        raise RuntimeError(f"应当正好收到一封重置邮件，实际 {len(messages)} 封")
    message = message_from_bytes(messages[0].read_bytes(), policy=policy.default)
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body is not None else ""
    links = [word for word in text.split() if "/password-reset/" in word and word.startswith("http")]
    if len(links) != 1:
        raise RuntimeError(f"重置邮件里应当正好有一个链接：\n{text}")
    return links[0]


if __name__ == "__main__":
    raise SystemExit(main())
