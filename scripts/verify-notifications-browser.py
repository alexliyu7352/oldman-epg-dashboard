#!/usr/bin/env python3
"""Exercise the shared notification UI in a real browser, plus this demo's example pages."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for import_root in (str(ROOT),):
    while import_root in sys.path:
        sys.path.remove(import_root)
    sys.path.insert(0, import_root)

from oldman.testing.browser import BrowserVerificationError  # noqa: E402
from oldman.testing.notifications import (  # noqa: E402
    FirefoxBiDi,
    HostContract,
    notification_gate_main,
)

EPG_HOST = HostContract(
    name="epg",
    home_path="/",
    login_path="/login",
    center_path="/user-notifications",
)


def verify_example_pages(client: FirefoxBiDi, context: str, base_url: str, evidence: dict[str, Any]) -> None:
    """This demo's own Firefox smoke: language switch, example pages, remote Modal, realtime table."""
    switched = client.evaluate(
        context,
        """
(() => {
  document.querySelector('[data-om-language-current]')?.click();
  const target = document.querySelector('[data-lang="zh-Hans"]');
  if (!(target instanceof HTMLElement)) return false;
  target.click();
  return true;
})()
""",
    )
    if switched is not True:
        raise BrowserVerificationError(
            "Firefox could not select Simplified Chinese"
        )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        language = client.evaluate(
            context,
            "document.documentElement.lang",
        )
        if isinstance(language, str) and language.casefold() == "zh-hans":
            evidence["language"] = language
            break
        time.sleep(0.1)
    else:
        raise BrowserVerificationError(
            "Firefox language switch did not reach Simplified Chinese"
        )
    # 语言切换会重载页面，之前累积的错误与后面的页面无关。
    client.console_errors.clear()

    def visit(path: str) -> None:
        client.command(
            "browsingContext.navigate",
            {
                "context": context,
                "url": f"{base_url}{path}",
                "wait": "complete",
            },
        )

    page_checks = (
        (
            "/examples/forms/basics",
            "document.querySelectorAll('form[data-om-form]').length >= 2",
            "Form",
        ),
        (
            "/examples/tables/json",
            "document.querySelectorAll(\"#example-projects-table [data-om-table-row]\").length > 0",
            "JSON Table",
        ),
    )
    for path, condition, label in page_checks:
        visit(path)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if client.evaluate(
                context,
                f"location.pathname === {json.dumps(path)} && "
                "document.documentElement.dataset.omReady === 'true' && "
                f"document.body.dataset.omExamplesReady === 'true' && ({condition})",
            ) is True:
                break
            time.sleep(0.1)
        else:
            raise BrowserVerificationError(f"Firefox {label} smoke failed")

    visit("/examples/modals/remote")
    modal_opened = client.evaluate(
        context,
        """
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let index = 0; index < 80; index += 1) {
const modal = document.querySelector('#remote-example-modal');
if (document.documentElement.dataset.omReady === 'true'
    && modal?.dataset.omComponentState === 'mounted') break;
await sleep(100);
  }
  document.querySelector('#remote-modal-opener')?.click();
  for (let index = 0; index < 80; index += 1) {
if (document.querySelector("#remote-example-modal [data-example-remote-step='1']")) return true;
await sleep(100);
  }
  return false;
})()
""",
    )
    if modal_opened is not True:
        raise BrowserVerificationError("Firefox remote Modal smoke failed")

    visit("/examples/tables/realtime")
    realtime_updated = client.evaluate(
        context,
        """
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let cell = null;
  for (let index = 0; index < 80; index += 1) {
cell = document.querySelector("[data-om-table-row] [data-om-column='cpu_percent']");
if (location.pathname === '/examples/tables/realtime'
    && document.documentElement.dataset.omReady === 'true' && cell) break;
await sleep(100);
  }
  if (!cell) return { updated: false, reason: 'missing-cell', path: location.pathname };
  const initial = cell.textContent;
  for (let index = 0; index < 80; index += 1) {
await sleep(100);
if (cell.textContent !== initial) return { updated: true };
  }
  return {
updated: false,
reason: 'unchanged',
initial,
current: cell.textContent,
componentState: document.querySelector("[data-om-component='realtime-table']")?.dataset.omComponentState,
pageReady: document.body.dataset.omExamplesReady
  };
})()
""",
    )
    if not isinstance(realtime_updated, dict) or realtime_updated.get("updated") is not True:
        raise BrowserVerificationError(
            f"Firefox realtime SSE smoke failed: {realtime_updated}"
        )
    evidence["examples"] = ["form", "json-table", "modal", "sse"]
    unexpected_errors = [
        error
        for error in client.console_errors
        if "can’t establish a connection" not in error
        or not error.endswith("/user-events.")
    ]
    if unexpected_errors:
        raise BrowserVerificationError(
            f"Firefox console errors: {unexpected_errors}"
        )


def main() -> int:
    """Run the framework's notification gate against this project's URLs and pages."""
    return notification_gate_main(
        host=EPG_HOST,
        project_root=ROOT,
        extra_firefox_steps=verify_example_pages,
    )


if __name__ == "__main__":
    raise SystemExit(main())
