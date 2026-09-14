#!/usr/bin/env python3
"""Verify the Tailwind/Oldman shell in a real Chrome browser."""

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

ROOT = Path(__file__).resolve().parents[1]
LEGACY_GATE_PATH = ROOT / "scripts" / "verify-dashboard-browser.py"
SPEC = importlib.util.spec_from_file_location("oldman_dashboard_gate", LEGACY_GATE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load browser gate helpers from {LEGACY_GATE_PATH}")
LEGACY_GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LEGACY_GATE
SPEC.loader.exec_module(LEGACY_GATE)

CDPClient = LEGACY_GATE.CDPClient
DEFAULT_PASSWORD = LEGACY_GATE.DEFAULT_PASSWORD
DEFAULT_URL = LEGACY_GATE.DEFAULT_URL
DEFAULT_USERNAME = LEGACY_GATE.DEFAULT_USERNAME
VerificationResult = LEGACY_GATE.VerificationResult
VerificationError = LEGACY_GATE.VerificationError
configure_viewport = LEGACY_GATE.configure_viewport
create_page_websocket = LEGACY_GATE.create_page_websocket
find_free_port = LEGACY_GATE.find_free_port
launch_chrome = LEGACY_GATE.launch_chrome
login = LEGACY_GATE.login
navigate = LEGACY_GATE.navigate
save_screenshot = LEGACY_GATE.save_screenshot
wait_for_chrome_devtools = LEGACY_GATE.wait_for_chrome_devtools

SCREENSHOT_ROOT = Path(os.environ.get("OLDMAN_EPG_CHILD_SCREENSHOT_DIR", "/tmp")).expanduser().resolve()
DESKTOP_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-dashboard-desktop.png")
MOBILE_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-dashboard-mobile.png")
USERS_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-users-interactions.png")
MODAL_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-modal.png")
LOGIN_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-login.png")
CHART_LOADING_3G_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-tailwind-dashboard-chart-loading-3g.png")
SCREENSHOT_DIR = SCREENSHOT_ROOT / "tailwind_gate"

ROUTES = [
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
]

BUSINESS_MODAL_CHECKS = [
    ("/user-session", "#user-session-password-modal", "user session password", True),
    ("/upstream-records", "#upstream-record-raw-modal", "upstream raw payload", False),
    ("/logo-assets", "#logo-asset-compare-modal", "logo asset compare", False),
    ("/match-decisions", "#match-decision-edit-modal", "match decision edit", True),
    ("/notifications", "#notification-detail-modal", "notification detail", False),
]


def main() -> int:
    result = VerificationResult(desktopScreenshot=DESKTOP_SCREENSHOT, mobileScreenshot=MOBILE_SCREENSHOT)
    url = str(os.environ.get("OLDMAN_DASHBOARD_URL") or DEFAULT_URL)
    verify_tailwind(url, result)
    result.ok = not result.consoleErrors and not result.pageErrors and not result.badResponses
    print(json.dumps(result.as_json(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def verify_tailwind(url: str, result: VerificationResult) -> None:
    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="oldman-tailwind-chrome-")
    chrome = launch_chrome(port, user_data_dir)
    client: CDPClient | None = None
    try:
        if SCREENSHOT_DIR.exists():
            shutil.rmtree(SCREENSHOT_DIR)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        wait_for_chrome_devtools(chrome, port)
        client = CDPClient(create_page_websocket(port), result)
        assert client is not None
        client.command("Page.enable")
        client.command("Runtime.enable")
        client.command("Network.enable")
        client.command("Log.enable")

        configure_viewport(client, 1440, 1000, mobile=False)
        navigate(client, urllib.parse.urljoin(url, "/login"))
        wait_for_oldman_ready(client)
        result.pageErrors.extend(assertions("login page", login_page_js(client)))
        save_screenshot(client, LOGIN_SCREENSHOT)
        result.screenshots["tailwind-login"] = LOGIN_SCREENSHOT
        login_matrix_screenshot = screenshot_path("/login", "desktop")
        save_screenshot(client, str(login_matrix_screenshot))
        result.screenshots["route:/login:desktop"] = str(login_matrix_screenshot)

        login(
            client,
            url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )

        configure_viewport(client, 1440, 1000, mobile=False)
        for path in ROUTES:
            navigate(client, urllib.parse.urljoin(url, path))
            wait_for_oldman_ready(client)
            result.pageErrors.extend(assertions(f"{path} desktop route", route_structure_js(client, path, "desktop")))
            route_screenshot = screenshot_path(path, "desktop")
            save_screenshot(client, str(route_screenshot))
            result.screenshots[f"route:{path}:desktop"] = str(route_screenshot)

        configure_viewport(client, 2048, 1152, mobile=False)
        for path in ROUTES:
            navigate(client, urllib.parse.urljoin(url, path))
            wait_for_oldman_ready(client)
            result.pageErrors.extend(assertions(f"{path} wide route", route_structure_js(client, path, "wide")))
            route_screenshot = screenshot_path(path, "wide")
            save_screenshot(client, str(route_screenshot))
            result.screenshots[f"route:{path}:wide"] = str(route_screenshot)

        configure_viewport(client, 1220, 1000, mobile=False)
        for path in ROUTES:
            navigate(client, urllib.parse.urljoin(url, path))
            wait_for_oldman_ready(client)
            result.pageErrors.extend(assertions(f"{path} medium route", route_structure_js(client, path, "medium")))
            route_screenshot = screenshot_path(path, "medium")
            save_screenshot(client, str(route_screenshot))
            result.screenshots[f"route:{path}:medium"] = str(route_screenshot)

        navigate(client, url)
        wait_for_oldman_ready(client)
        result.pageErrors.extend(assertions("DM Sans font loading", font_loading_js(client)))
        result.pageErrors.extend(assertions("desktop dashboard", desktop_dashboard_js(client)))
        save_screenshot(client, DESKTOP_SCREENSHOT)
        result.screenshots["tailwind-desktop-dashboard"] = DESKTOP_SCREENSHOT
        assert_dashboard_chart_loading_overlay_3g(port, url, result)

        navigate(client, urllib.parse.urljoin(url, "/users"))
        wait_for_oldman_ready(client)
        result.pageErrors.extend(assertions("users interactions", users_interactions_js(client)))
        save_screenshot(client, USERS_SCREENSHOT)
        result.screenshots["tailwind-users-interactions"] = USERS_SCREENSHOT

        navigate(client, urllib.parse.urljoin(url, "/upstream-records"))
        wait_for_oldman_ready(client)
        result.pageErrors.extend(assertions("modal interactions", modal_interactions_js(client)))
        save_screenshot(client, MODAL_SCREENSHOT)
        result.screenshots["tailwind-modal"] = MODAL_SCREENSHOT
        result.pageErrors.extend(assertions("modal close", modal_close_js(client)))

        for path, target, label, expect_form in BUSINESS_MODAL_CHECKS:
            navigate(client, urllib.parse.urljoin(url, path))
            wait_for_oldman_ready(client)
            result.pageErrors.extend(assertions(f"{label} business modal", business_modal_js(client, target, label, expect_form)))

        configure_viewport(client, 390, 844, mobile=True)
        for path in ROUTES:
            navigate(client, urllib.parse.urljoin(url, path))
            wait_for_oldman_ready(client)
            result.pageErrors.extend(assertions(f"{path} mobile route", route_structure_js(client, path, "mobile")))
            route_screenshot = screenshot_path(path, "mobile")
            save_screenshot(client, str(route_screenshot))
            result.screenshots[f"route:{path}:mobile"] = str(route_screenshot)

        navigate(client, url)
        wait_for_oldman_ready(client)
        result.pageErrors.extend(assertions("mobile dashboard", mobile_dashboard_js(client)))
        save_screenshot(client, MOBILE_SCREENSHOT)
        result.screenshots["tailwind-mobile-dashboard"] = MOBILE_SCREENSHOT
        result.screenshots["tailwind-route-screenshot-dir"] = str(SCREENSHOT_DIR)
        client.pump(0.5)
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


def wait_for_oldman_ready(client: CDPClient) -> None:
    ready = client.evaluate(
        """
        new Promise((resolve) => {
          let count = 0;
          const tick = () => {
            if (document.documentElement.dataset.omReady === "true") {
              resolve(true);
              return;
            }
            if (count > 80) {
              resolve(false);
              return;
            }
            count += 1;
            setTimeout(tick, 50);
          };
          tick();
        })
        """,
        timeout=6,
    )
    if ready is not True:
        raise VerificationError("Timed out waiting for oldman ready state")


def screenshot_path(route: str, viewport: str) -> Path:
    """Return a stable screenshot path for a route and viewport."""
    slug = route.strip("/").replace("/", "_") or "dashboard"
    return SCREENSHOT_DIR / f"{slug}-{viewport}.png"


def assertions(label: str, payload: object) -> list[str]:
    if not isinstance(payload, dict):
        failures = ["browser assertion returned no object"]
    elif "failures" not in payload:
        failures = ["browser assertion omitted failures"]
    elif not isinstance(payload["failures"], list):
        failures = ["browser assertion returned malformed failures"]
    else:
        failures = [str(item) for item in payload["failures"]]
    return [f"{label}: {failure}" for failure in failures]


def font_loading_js(client: CDPClient) -> dict[str, object]:
    """Verify the shared shell uses a real loaded DM Sans 700 webfont."""
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  await document.fonts.ready;
  const normalizeFamily = (value) => String(value || "").replace(/["']/g, "").trim().toLowerCase();
  const faces = Array.from(document.fonts).filter((face) => normalizeFamily(face.family) === "dm sans");
  const weight700 = faces.find((face) => String(face.weight) === "700");
  if (!weight700) failures.push("DM Sans 700 FontFace is not registered");
  else if (weight700.status !== "loaded") failures.push(`DM Sans 700 FontFace status is ${weight700.status}`);
  if (!document.fonts.check('700 16px "DM Sans"')) failures.push("Font Loading API rejected DM Sans 700");
  const fontResources = performance.getEntriesByType("resource").map((entry) => decodeURIComponent(entry.name));
  if (!fontResources.some((name) => /dm-sans[^/]*700-normal[^/]*\.woff2(?:\?|$)/.test(name))) {
    failures.push("DM Sans 700 woff2 was not fetched by the real page");
  }
  return { failures, faces: faces.map((face) => ({ weight: face.weight, status: face.status })) };
})()
        """,
    )


def route_structure_js(client: CDPClient, expected_path: str, viewport: str) -> dict[str, object]:
    return client.evaluate(
        rf"""
(async () => {{
  const failures = [];
  const expectedPath = {json.dumps(expected_path)};
  const viewport = {json.dumps(viewport)};
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {{
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }};
  for (let i = 0; i < 40; i += 1) {{
    const partial = document.querySelector("[data-om-table-partial]");
    if (!partial || partial.querySelector(".om-table tbody tr")) break;
    await sleep(75);
  }}
  const transparent = (value) => {{
    if (!value || value === "transparent" || value === "rgba(0, 0, 0, 0)") return true;
    const alpha = String(value).match(/\/\s*([0-9.e+-]+)\s*\)$/);
    return alpha ? Number(alpha[1]) <= 0.001 : false;
  }};
  const legacyClassTokens = () => {{
    const banned = new Set([
      "container-fluid", "row", "d-sm-flex", "d-lg-flex", "bg-light", "bg-light-subtle", "text-muted",
      "img-fluid", "h-100", "w-100", "me-1", "me-2", "me-3", "ms-1", "ms-2", "ms-3",
      "breadcrumb", "breadcrumb-item", "alert", "btn", "form-label", "text-uppercase",
      "material-shadow-none", "position-absolute", "position-relative", "text-reset",
      "text-decoration-underline", "text-decoration-none", "page-title-box"
    ]);
    const matches = [];
    for (const element of document.querySelectorAll("[class]")) {{
      for (const token of String(element.getAttribute("class") || "").split(/\s+/).filter(Boolean)) {{
        if (banned.has(token) || /^col(-(sm|md|lg|xl|xxl))?-\d+$/.test(token) || /^fs-\d+$/.test(token)) {{
          matches.push(token);
        }}
      }}
    }}
    return [...new Set(matches)].sort();
  }};
  const readableTextFailures = () => {{
    const items = [];
    const candidates = Array.from(document.querySelectorAll("h1, h2, h3, label, .om-table td, .om-table th, .om-card, .om-field"))
      .filter(visible)
      .slice(0, 80);
    for (const element of candidates) {{
      const style = getComputedStyle(element);
      const color = style.color.replace(/\s+/g, "");
      if (color === "rgb(161,161,170)" || color === "rgb(212,212,216)") {{
        items.push(`${{element.tagName.toLowerCase()}} uses very low-emphasis zinc text as primary content`);
      }}
    }}
    return items;
  }};
  const responsiveGridFailures = () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const grids = Array.from(document.querySelectorAll(".md\\:grid-cols-12")).filter(visible);
    for (const grid of grids) {{
      const gridRect = grid.getBoundingClientRect();
      if (gridRect.width < 640) continue;
      const children = Array.from(grid.children).filter((child) => visible(child));
      for (const child of children) {{
        const childRect = child.getBoundingClientRect();
        if (childRect.width < 180) {{
          const text = (child.textContent || "").trim().replace(/\s+/g, " ").slice(0, 80);
          items.push(`responsive grid child is too narrow: ${{Math.round(childRect.width)}}px in ${{Math.round(gridRect.width)}}px grid (${{text}})`);
        }}
      }}
    }}
    return items;
  }};
  const layoutContainmentFailures = () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const main = document.querySelector(".oldman-main, #oldman-main, main");
    const container = document.querySelector(".oldman-page-container");
    if (!visible(main) || !visible(container)) return items;
    const mainRect = main.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const mainStyle = getComputedStyle(main);
    const mainContentWidth = mainRect.width - parseFloat(mainStyle.paddingLeft || "0") - parseFloat(mainStyle.paddingRight || "0");
    const offset = containerRect.left - mainRect.left;
    if (mainRect.width > 1200 && containerRect.width < mainContentWidth * 0.92) {{
      items.push(`page container is too narrow on ${{viewport}}: ${{Math.round(containerRect.width)}}px of ${{Math.round(mainContentWidth)}}px`);
    }}
    if (mainRect.width > 1200 && offset > 64) {{
      items.push(`page container is visually centered instead of aligned to content start: ${{Math.round(offset)}}px offset`);
    }}
    return items;
  }};
  const sidebarVisualFailures = () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const sidebar = document.querySelector("[data-om-sidebar]");
    const scroll = document.querySelector(".oldman-sidebar-scroll");
    const nav = document.querySelector("#navbar-nav");
    if (!visible(sidebar) || !scroll || !nav) return items;
    const primaryColor = getComputedStyle(document.documentElement).getPropertyValue("--color-primary").trim();
    if (scroll.hasAttribute("data-simplebar")) items.push("sidebar scroll container is still initialized as SimpleBar");
    if (nav.hasAttribute("data-simplebar")) items.push("sidebar nav list is still initialized as SimpleBar");
    if (scroll.querySelector(".simplebar-wrapper, .simplebar-content-wrapper")) items.push("sidebar contains nested SimpleBar wrapper markup");
    if (nav.querySelector(":scope > .simplebar-wrapper, :scope > .simplebar-content-wrapper")) items.push("sidebar nav list contains invalid SimpleBar wrapper children");
    if (scroll.scrollHeight <= scroll.clientHeight + 2 && scroll.classList.contains("simplebar-scrollable-y")) {{
      items.push("sidebar shows a vertical scroll affordance while content fits");
    }}
    const activeItems = Array.from(sidebar.querySelectorAll("[data-om-menu-item].active"));
    const openItems = Array.from(sidebar.querySelectorAll("[data-om-menu-item].open"));
    if (activeItems.length > 1) items.push(`sidebar has multiple active root groups: ${{activeItems.length}}`);
    if (openItems.length > 1) items.push(`sidebar has multiple open root groups: ${{openItems.length}}`);
    for (const toggle of Array.from(sidebar.querySelectorAll("[data-om-menu-toggle]")).filter(visible)) {{
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      const item = toggle.closest("[data-om-menu-item]");
      const style = getComputedStyle(toggle);
      const hasActiveClass = toggle.classList.contains("active") || item?.classList.contains("active") || item?.classList.contains("open");
      if (!expanded && hasActiveClass) items.push(`closed sidebar group keeps active/open class: ${{toggle.textContent.trim().replace(/\\s+/g, " ")}}`);
      if (!expanded && style.color === primaryColor) items.push(`closed sidebar group keeps primary text color: ${{toggle.textContent.trim().replace(/\\s+/g, " ")}}`);
      if (!expanded && !transparent(style.backgroundColor)) items.push(`closed sidebar group keeps active background: ${{toggle.textContent.trim().replace(/\\s+/g, " ")}}`);
    }}
    return items;
  }};
  const tableVisualFailures = () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const shells = Array.from(document.querySelectorAll(".om-table-shell")).filter(visible);
    for (const shell of shells.slice(0, 8)) {{
      const table = shell.querySelector(".om-table");
      const thead = table?.querySelector("thead");
      if (!visible(table)) {{
        items.push("visible table shell is missing a visible table");
        continue;
      }}
      const shellRect = shell.getBoundingClientRect();
      const tableRect = table.getBoundingClientRect();
      if (shellRect.width > 800 && tableRect.width < shellRect.width * 0.96) {{
        items.push(`table does not fill its shell: ${{Math.round(tableRect.width)}}px table in ${{Math.round(shellRect.width)}}px shell`);
      }}
      if (!visible(thead)) {{
        items.push("table header is not visible on desktop/tablet");
      }} else {{
        const headRect = thead.getBoundingClientRect();
        const headerCells = Array.from(thead.querySelectorAll("th")).filter(visible);
        const style = getComputedStyle(thead);
        if (headRect.height < 24) items.push(`table header is too short: ${{Math.round(headRect.height)}}px`);
        if (headerCells.length < 2) items.push(`table header has too few visible cells: ${{headerCells.length}}`);
        if (transparent(style.backgroundColor)) items.push("table header has transparent background");
      }}
    }}
    return items;
  }};
  const cardTableFailures = () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const bodies = Array.from(document.querySelectorAll(".om-card-body")).filter((body) => {{
      const children = Array.from(body.children).filter(visible);
      return children.length === 1 && children[0].classList.contains("om-table-scroll");
    }});
    for (const body of bodies.slice(0, 8)) {{
      const scroll = body.querySelector(".om-table-scroll");
      const table = scroll?.querySelector(".om-table");
      const header = body.closest(".om-card")?.querySelector(".om-card-header");
      if (!visible(scroll) || !visible(table)) continue;
      const bodyStyle = getComputedStyle(body);
      const bodyRect = body.getBoundingClientRect();
      const scrollRect = scroll.getBoundingClientRect();
      const tableRect = table.getBoundingClientRect();
      if (parseFloat(bodyStyle.paddingTop) > 1 || parseFloat(bodyStyle.paddingLeft) > 1 || parseFloat(bodyStyle.paddingRight) > 1) {{
        items.push("pure table card body still has inner padding");
      }}
      if (header) {{
        const headerRect = header.getBoundingClientRect();
        if (Math.abs(scrollRect.top - headerRect.bottom) > 2) {{
          items.push(`table card has a gap below its header: ${{Math.round(scrollRect.top - headerRect.bottom)}}px`);
        }}
      }}
      if (scrollRect.width < bodyRect.width - 2) {{
        items.push(`table scroll area does not fill card body: ${{Math.round(scrollRect.width)}}px of ${{Math.round(bodyRect.width)}}px`);
      }}
      if (tableRect.width < scrollRect.width - 2 && scrollRect.width > 720) {{
        items.push(`table does not fill card scroll area: ${{Math.round(tableRect.width)}}px of ${{Math.round(scrollRect.width)}}px`);
      }}
    }}
    return items;
  }};
  const tableFooterFailures = () => {{
    const items = [];
    const controls = Array.from(document.querySelectorAll("[data-om-table-page-size-control]")).filter(visible);
    for (const control of controls.slice(0, 8)) {{
      const root = control.closest("[data-om-component='table']");
      const shell = root?.querySelector(".om-table-shell");
      const summary = root?.querySelector("[data-om-table-summary]");
      if (!visible(shell) || !visible(summary)) continue;
      const controlRect = control.getBoundingClientRect();
      const shellRect = shell.getBoundingClientRect();
      const summaryRect = summary.getBoundingClientRect();
      if (controlRect.width < 56) {{
        items.push("table page-size control is too narrow to show its value");
      }}
      if (control.options.length === 0 || !control.selectedOptions[0]?.textContent?.trim()) {{
        items.push("table page-size control has no visible selected value");
      }}
      if (controlRect.top < shellRect.bottom - 2) {{
        items.push("table page-size control is rendered above the table instead of in the footer");
      }}
      if (controlRect.top > summaryRect.bottom + 24) {{
        items.push("table page-size control is not grouped with the summary");
      }}
      if (viewport !== "mobile" && controlRect.left > summaryRect.left + 2) {{
        items.push("table page-size control appears after the summary instead of before it");
      }}
    }}
    return items;
  }};
  const mobileTableLayoutFailures = () => {{
    if (viewport !== "mobile") return [];
    const items = [];
    const tables = Array.from(document.querySelectorAll(".om-table"))
      .filter(visible)
      .filter((table) => table.querySelector("thead"));
    for (const table of tables.slice(0, 8)) {{
      const scroll = table.closest(".om-table-scroll");
      const thead = table.querySelector("thead");
      const tbody = table.querySelector("tbody");
      const firstRow = table.querySelector("tbody tr");
      const firstCell = table.querySelector("tbody td");
      const actionCell = table.querySelector('tbody td[data-om-column="action"]');
      const tableStyle = getComputedStyle(table);
      const scrollStyle = scroll ? getComputedStyle(scroll) : null;
      if (tableStyle.display === "block") items.push("mobile table is still rendered as card/block layout");
      if (visible(thead) === false) items.push("mobile table header is hidden; expected horizontal table layout");
      if (tbody && getComputedStyle(tbody).display === "flex") items.push("mobile table body is still flex card layout");
      if (firstRow && getComputedStyle(firstRow).display === "block") items.push("mobile table rows are still block cards");
      if (firstCell && getComputedStyle(firstCell).display.includes("grid")) items.push("mobile table cells are still grid label/value cards");
      if (firstCell && getComputedStyle(firstCell, "::before").content !== "none") items.push("mobile table cells still render pseudo labels");
      if (scroll && scrollStyle && !["auto", "scroll"].includes(scrollStyle.overflowX)) items.push("mobile table scroll container does not allow horizontal scrolling");
      if (scroll && table.getBoundingClientRect().width <= scroll.getBoundingClientRect().width + 8) {{
        items.push("mobile table does not preserve a wider scrollable table width");
      }}
      if (actionCell && getComputedStyle(actionCell).position !== "sticky") items.push("mobile table action column is not sticky");
    }}
    return items;
  }};
  const tableDropdownFailures = async () => {{
    if (viewport === "mobile") return [];
    const items = [];
    const inViewport = (element) => {{
      if (!visible(element)) return false;
      const rect = element.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
    }};
    const toggles = Array.from(document.querySelectorAll(".om-table [data-om-dropdown-toggle]")).filter(visible);
    if (!toggles.length) return items;
    const toggle = toggles.find(inViewport) || toggles[0];
    if (!inViewport(toggle)) {{
      toggle.scrollIntoView({{ block: "center", inline: "nearest" }});
      await sleep(120);
    }}
    toggle.click();
    await sleep(150);
    const wrapper = toggle.closest(".om-dropdown");
    const menu = wrapper?.querySelector(".om-dropdown-menu");
    if (!visible(menu)) {{
      items.push("table row action dropdown did not become visible");
      return items;
    }}
    const buttonRect = toggle.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.width > 260) items.push(`table row action dropdown is too wide: ${{Math.round(menuRect.width)}}px`);
    if (menuRect.left < -2 || menuRect.right > window.innerWidth + 2) items.push("table row action dropdown overflows viewport");
    if (menu.classList.contains("om-dropdown-menu-end") && Math.abs(menuRect.right - buttonRect.right) > 24) {{
      items.push(`table row action dropdown is detached horizontally: ${{Math.round(menuRect.right - buttonRect.right)}}px from trigger`);
    }}
    const opensBelow = Math.abs(menuRect.top - buttonRect.bottom) <= 24;
    const opensAbove = Math.abs(menuRect.bottom - buttonRect.top) <= 24;
    if (!opensBelow && !opensAbove) {{
      items.push(`table row action dropdown is detached vertically: top ${{Math.round(menuRect.top)}}px, trigger bottom ${{Math.round(buttonRect.bottom)}}px`);
    }}
    document.dispatchEvent(new KeyboardEvent("keydown", {{ key: "Escape", bubbles: true }}));
    document.body.click();
    await sleep(50);
    return items;
  }};
  if (document.documentElement.dataset.omReady !== "true") failures.push("page did not reach omReady=true");
  if (location.pathname !== expectedPath) failures.push(`expected ${{expectedPath}}, got ${{location.pathname}}`);
  if (!document.querySelector("main, #oldman-main")) failures.push("missing main or oldman-main frame");
  if (document.querySelector('#page-topbar input[type="search"], #page-topbar input[placeholder*="Search something"]')) failures.push("topbar still contains an unimplemented search control");
  if (document.querySelectorAll("h1").length !== 1) failures.push(`expected exactly one h1, got ${{document.querySelectorAll("h1").length}}`);
  if (document.querySelector(".page-title-box")) failures.push("legacy page-title-box remains");
  if (document.querySelector(".container-fluid .page-content")) failures.push("nested container-fluid page-content remains");
  if (document.querySelector('script[src*="bootstrap"], link[href*="bootstrap"]')) failures.push("bootstrap asset is loaded");
  const legacyTokens = legacyClassTokens();
  if (legacyTokens.length) failures.push(`legacy Bootstrap class tokens remain: ${{legacyTokens.join(", ")}}`);
  if (document.documentElement.scrollWidth > window.innerWidth + 2) failures.push(`${{viewport}} has horizontal overflow ${{document.documentElement.scrollWidth}} > ${{window.innerWidth}}`);

  for (const selector of [".om-card", ".om-table", ".om-field", ".om-dropdown-menu", ".om-modal"]) {{
    const elements = Array.from(document.querySelectorAll(selector)).filter(visible);
    for (const element of elements.slice(0, 24)) {{
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (rect.width < 1 || rect.height < 1) failures.push(`${{selector}} has invalid dimensions`);
      if (viewport !== "mobile" && selector === ".om-card" && rect.width < 220) failures.push(`${{selector}} is too narrow for desktop/tablet layout: ${{Math.round(rect.width)}}px`);
      if ([".om-card", ".om-table", ".om-field", ".om-dropdown-menu", ".om-modal"].includes(selector) && transparent(style.backgroundColor)) {{
        failures.push(`${{selector}} has transparent background while visible`);
      }}
      if (rect.left < -2 || rect.right > window.innerWidth + 2) {{
        const scrollParent = element.closest(".om-table-scroll");
        if (selector === ".om-table" && scrollParent && scrollParent.getBoundingClientRect().right <= window.innerWidth + 2) {{
          continue;
        }}
        failures.push(`${{selector}} overflows viewport horizontally`);
      }}
    }}
  }}
  failures.push(...readableTextFailures());
  failures.push(...responsiveGridFailures());
  failures.push(...layoutContainmentFailures());
  failures.push(...sidebarVisualFailures());
  failures.push(...tableVisualFailures());
  failures.push(...cardTableFailures());
  failures.push(...tableFooterFailures());
  failures.push(...mobileTableLayoutFailures());
  failures.push(...(await tableDropdownFailures()));
  return {{ failures }};
}})()
        """,
        timeout=12,
    ) or {}


def business_modal_js(client: CDPClient, target: str, label: str, expect_form: bool) -> dict[str, object]:
    return client.evaluate(
        rf"""
(async () => {{
  const failures = [];
  const target = {json.dumps(target)};
  const label = {json.dumps(label)};
  const expectForm = {json.dumps(expect_form)};
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {{
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }};
  const openState = (modal) => modal && !modal.hidden && modal.getAttribute("aria-hidden") !== "true" && (modal.classList.contains("is-open") || modal.classList.contains("show"));
  const waitForClosed = async (modal) => {{
    for (let i = 0; i < 12; i += 1) {{
      if (!openState(modal) && !visible(modal) && !document.querySelector(".om-modal-backdrop")) return true;
      await sleep(75);
    }}
    return false;
  }};
  let trigger = null;
  for (let i = 0; i < 60; i += 1) {{
    trigger = document.querySelector(`[data-om-modal-target="${{target}}"][data-om-modal-url], [data-om-modal-target="${{target}}"]`);
    if (trigger) break;
    await sleep(100);
  }}
  if (!trigger) return {{ failures: [`missing ${{label}} modal trigger for ${{target}}`] }};
  if (!visible(trigger)) {{
    const row = trigger.closest("[data-om-table-row], tr");
    const dropdownToggle = row?.querySelector("[data-om-dropdown-toggle]");
    if (dropdownToggle) {{
      dropdownToggle.click();
      await sleep(150);
    }}
  }}
  if (!visible(trigger)) failures.push(`${{label}} modal trigger is not visible after opening row actions`);
  trigger.click();
  for (let i = 0; i < 60 && !openState(document.querySelector(target)); i += 1) {{
    await sleep(100);
  }}
  const modal = document.querySelector(target);
  const surface = modal?.querySelector(".om-modal-surface");
  if (!openState(modal)) failures.push(`${{label}} modal did not open`);
  if (!visible(surface)) failures.push(`${{label}} modal surface is not visible`);
  if (expectForm && !modal?.querySelector("form")) failures.push(`${{label}} modal did not load form content`);
  if (!expectForm && !modal?.querySelector(".om-modal-body")) failures.push(`${{label}} modal body is missing`);
  modal?.querySelector("[data-om-modal-close]")?.click();
  if (!(await waitForClosed(modal))) failures.push(`${{label}} modal did not close`);
  return {{ failures }};
}})()
        """,
        timeout=12,
    ) or {}


def login_page_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(() => {
  const failures = [];
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const area = (element) => {
    const rect = element.getBoundingClientRect();
    return Math.max(0, rect.width) * Math.max(0, rect.height);
  };
  if (document.documentElement.dataset.omReady !== "true") failures.push("login page did not reach omReady=true");
  if (location.pathname !== "/login") failures.push(`expected /login, got ${location.pathname}`);
  if (!visible(document.querySelector('form[action="/login"]'))) failures.push("login form is not visible");
  if (!visible(document.querySelector("#username"))) failures.push("username field is not visible");
  if (!visible(document.querySelector("#password-input"))) failures.push("password field is not visible");
  if (!visible(document.querySelector(".oldman-brand-mark"))) failures.push("login brand mark is not visible");
  if (!visible(document.querySelector("#login-lang-img"))) failures.push("login language selector is not visible");
  if (document.querySelector(".auth-page-wrapper, .auth-one-bg, .bg-overlay, .shape")) failures.push("legacy auth background nodes remain");
  if (document.querySelector('script[src*="bootstrap"], link[href*="bootstrap"]')) failures.push("bootstrap asset is loaded on login");
  if (document.documentElement.scrollWidth > window.innerWidth + 2) failures.push(`login has horizontal overflow ${document.documentElement.scrollWidth} > ${window.innerWidth}`);

  const blackBlocks = Array.from(document.querySelectorAll("body *")).filter((element) => {
    const style = getComputedStyle(element);
    const bg = style.backgroundColor.replace(/\s+/g, "");
    return (bg === "rgb(0,0,0)" || bg === "rgba(0,0,0,1)") && area(element) > window.innerWidth * 80;
  });
  if (blackBlocks.length) failures.push(`login contains large black blocks: ${blackBlocks.length}`);

  const formPanel = document.querySelector('form[action="/login"]')?.closest("section, .om-card, div");
  if (formPanel) {
    const rect = formPanel.getBoundingClientRect();
    if (rect.left < -1 || rect.top < -1 || rect.right > window.innerWidth + 1) failures.push("login form panel is outside viewport");
  }
  return { failures };
})()
        """,
        timeout=5,
    )


def desktop_dashboard_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const transparent = (value) => {
    if (!value || value === "transparent" || value === "rgba(0, 0, 0, 0)") return true;
    const alpha = String(value).match(/\/\s*([0-9.e+-]+)\s*\)$/);
    return alpha ? Number(alpha[1]) <= 0.001 : false;
  };
  const legacyClassTokens = () => {
    const banned = new Set([
      "container-fluid", "row", "d-sm-flex", "d-lg-flex", "bg-light", "bg-light-subtle", "text-muted",
      "img-fluid", "h-100", "w-100", "me-1", "me-2", "me-3", "ms-1", "ms-2", "ms-3",
      "breadcrumb", "breadcrumb-item", "alert", "btn", "form-label", "text-uppercase",
      "material-shadow-none", "position-absolute", "position-relative", "text-reset",
      "text-decoration-underline", "text-decoration-none"
    ]);
    const matches = [];
    for (const element of document.querySelectorAll("[class]")) {
      for (const token of String(element.getAttribute("class") || "").split(/\s+/).filter(Boolean)) {
        if (banned.has(token) || /^col(-(sm|md|lg|xl|xxl))?-\d+$/.test(token) || /^fs-\d+$/.test(token)) {
          matches.push(token);
        }
      }
    }
    return [...new Set(matches)].sort();
  };

  if (document.documentElement.dataset.omReady !== "true") failures.push("page did not reach omReady=true");
  if (document.querySelector('script[src*="bootstrap"], link[href*="bootstrap"]')) failures.push("bootstrap asset is loaded");
  const legacyTokens = legacyClassTokens();
  if (legacyTokens.length) failures.push(`legacy Bootstrap class tokens remain: ${legacyTokens.join(", ")}`);
  if (!document.querySelector("#oldman-main")) failures.push("missing oldman-main frame");
  if (!document.querySelector("#oldman-sidebar-nav")) failures.push("missing sidebar frame");
  if (!document.querySelector("#page-topbar")) failures.push("missing topbar");
  if (document.querySelector('#page-topbar input[type="search"], #page-topbar input[placeholder*="Search something"]')) {
    failures.push("topbar still contains an unimplemented search control");
  }

  const sidebar = document.querySelector("[data-om-sidebar]");
  if (!visible(sidebar)) failures.push("desktop sidebar is not visible");
  if (sidebar && sidebar.getBoundingClientRect().width < 240) failures.push(`desktop sidebar width is too small: ${sidebar.getBoundingClientRect().width}`);
  const topbarInteractionFailures = async () => {
    const items = [];
    const html = document.documentElement;
    const topbar = document.querySelector("#page-topbar");
    const sidebar = document.querySelector("[data-om-sidebar]");
    const content = document.querySelector(".oldman-page-content");
    const hamburger = document.querySelector("#topnav-hamburger-icon");
    const themeToggle = document.querySelector(".light-dark-mode");
    const languageButton = document.querySelector("#header-lang-img")?.closest("button");
    const notificationToggle = document.querySelector("#page-header-notifications-dropdown");
    const notificationMenu = document.querySelector("#notificationDropdown [data-om-dropdown-menu]");
    const background = (element) => element ? getComputedStyle(element).backgroundColor.replace(/\s+/g, "") : "";

    if (!topbar || !sidebar || !content || !hamburger) {
      items.push("missing shell elements required for topbar interaction checks");
    } else {
      document.body.click();
      html.setAttribute("data-sidebar-size", "lg");
      document.body.classList.remove("vertical-sidebar-enable");
      delete html.dataset.omSidebarOpen;
      await sleep(80);
      const expandedWidth = sidebar.getBoundingClientRect().width;
      const expandedMargin = parseFloat(getComputedStyle(content).marginLeft || "0");
      hamburger.click();
      await sleep(220);
      const collapsedWidth = sidebar.getBoundingClientRect().width;
      const collapsedMargin = parseFloat(getComputedStyle(content).marginLeft || "0");
      if (html.getAttribute("data-sidebar-size") !== "sm") items.push("desktop hamburger did not set data-sidebar-size=sm");
      if (collapsedWidth >= expandedWidth - 80) items.push(`desktop sidebar did not visually collapse: ${Math.round(expandedWidth)}px to ${Math.round(collapsedWidth)}px`);
      if (collapsedMargin >= expandedMargin - 80) items.push(`page content margin did not follow collapsed sidebar: ${Math.round(expandedMargin)}px to ${Math.round(collapsedMargin)}px`);
      if (document.body.classList.contains("vertical-sidebar-enable") || html.dataset.omSidebarOpen === "true") {
        items.push("desktop hamburger leaked mobile sidebar overlay state");
      }
      hamburger.click();
      await sleep(220);
      if (html.getAttribute("data-sidebar-size") !== "lg") items.push("desktop hamburger did not expand sidebar back to lg");
    }

    if (!themeToggle || !topbar) {
      items.push("missing theme toggle");
    } else {
      html.setAttribute("data-theme", "light");
      await sleep(80);
      const lightTopbarBg = background(topbar);
      themeToggle.click();
      await sleep(120);
      const darkTopbarBg = background(topbar);
      if (html.getAttribute("data-theme") !== "dark") items.push("dark mode toggle did not set data-theme=dark");
      if (darkTopbarBg === lightTopbarBg) items.push("dark mode toggle did not change topbar background");
      const darkTableBg = background(document.querySelector(".om-table"));
      const darkFieldBg = background(document.querySelector(".om-field, .om-select"));
      if (darkTableBg === "rgb(255,255,255)" || darkTableBg === "#fff" || darkTableBg === "#ffffff") {
        items.push("dark mode left table surface on a white background");
      }
      if (darkFieldBg === "rgb(255,255,255)" || darkFieldBg === "#fff" || darkFieldBg === "#ffffff") {
        items.push("dark mode left form controls on a white background");
      }
      themeToggle.click();
      await sleep(80);
      if (html.getAttribute("data-theme") !== "light") items.push("dark mode toggle did not restore data-theme=light");
    }

    if (!languageButton) {
      items.push("missing language switcher button");
    } else {
      document.body.click();
      const flag = document.querySelector("#header-lang-img");
      const flagRect = flag?.getBoundingClientRect();
      if (!flagRect || flagRect.width < 20 || flagRect.height < 14) items.push("language flag is too small or not visible");
      const languageMenu = languageButton.closest(".om-dropdown")?.querySelector("[data-om-dropdown-menu]");
      languageButton.click();
      await sleep(120);
      if (!visible(languageMenu) || languageMenu.hidden || languageMenu.classList.contains("hidden")) items.push("language dropdown did not open");
      const beforeFlag = flag?.getAttribute("src") || "";
      const option = Array.from(languageMenu?.querySelectorAll("[data-lang]") || []).find((item) => item.getAttribute("data-lang") === "zh-Hans");
      option?.click();
      await sleep(250);
      const afterFlag = flag?.getAttribute("src") || "";
      if (!option) items.push("missing explicit zh-Hans language option");
      if (option && beforeFlag === afterFlag) items.push("language selection did not update current flag");
      if (option && option.getAttribute("aria-pressed") !== "true") items.push("language selection did not update aria-pressed state");
      document.body.click();
      languageButton.click();
      await sleep(120);
      const restoreOption = Array.from(languageMenu?.querySelectorAll("[data-lang]") || []).find((item) => item.getAttribute("data-lang") === "en");
      restoreOption?.click();
      await sleep(250);
      if (!restoreOption) items.push("missing explicit en language restore option");
      if (restoreOption && restoreOption.getAttribute("aria-pressed") !== "true") items.push("language selection did not restore en state");
      if (restoreOption && (flag?.getAttribute("src") || "") !== beforeFlag) items.push("language selection did not restore current flag");
      document.body.click();
    }

    if (!notificationToggle || !notificationMenu) {
      items.push("missing notification dropdown");
    } else {
      document.body.click();
      notificationToggle.click();
      await sleep(120);
      if (!visible(notificationMenu) || notificationMenu.hidden || notificationMenu.classList.contains("hidden")) {
        items.push("notification dropdown did not open");
      } else {
        const menuStyle = getComputedStyle(notificationMenu);
        const scrolls = notificationMenu.style.overflowY === "auto" || menuStyle.overflowY === "scroll";
        if (scrolls && notificationMenu.scrollHeight <= notificationMenu.clientHeight + 1) {
          items.push("notification dropdown forces a scrollbar while content fits");
        }
        if (notificationMenu.querySelector("[data-simplebar], .simplebar-wrapper, .simplebar-content-wrapper")) {
          items.push("notification dropdown still contains SimpleBar markup");
        }
      }
      document.body.click();
      await sleep(80);
    }

    return items;
  };
  const sidebarStateFailures = async () => {
    const items = Array.from(document.querySelectorAll("[data-om-sidebar] [data-om-menu-item]")).filter(visible);
    const toggles = Array.from(document.querySelectorAll("[data-om-sidebar] [data-om-menu-toggle]")).filter(visible);
    if (toggles.length < 2) return ["sidebar has fewer than two expandable groups to validate"];
    const bg = (element) => getComputedStyle(element).backgroundColor.replace(/\s+/g, "");
    const transparent = (value) => {
      if (!value || value === "transparent" || value === "rgba(0,0,0,0)" || value === "rgba(0, 0, 0, 0)") return true;
      const alpha = String(value).match(/\/\s*([0-9.e+-]+)\s*\)$/);
      return alpha ? Number(alpha[1]) <= 0.001 : false;
    };
    const secondToggle = toggles.find((toggle) => !toggle.textContent.includes("Dashboard")) || toggles[0];
    secondToggle.click();
    await sleep(150);
    const thirdToggle = toggles.find((toggle) => toggle !== secondToggle && !toggle.textContent.includes("Dashboard")) || toggles[1];
    thirdToggle.click();
    await sleep(150);
    const activeRouteLink = document.querySelector("[data-om-sidebar] .oldman-menu-link.active[aria-expanded='false']");
    const openItems = items.filter((item) => item.classList.contains("open"));
    const expandedToggles = toggles.filter((toggle) => toggle.getAttribute("aria-expanded") === "true");
    const sidebarFailures = [];
    if (openItems.length > 1) sidebarFailures.push(`sidebar keeps multiple open groups: ${openItems.length}`);
    if (expandedToggles.length > 1) sidebarFailures.push(`sidebar keeps multiple expanded toggles: ${expandedToggles.length}`);
    if (openItems.length === 1) {
      const openToggle = openItems[0].querySelector("[data-om-menu-toggle]");
      if (openToggle?.getAttribute("aria-expanded") !== "true") sidebarFailures.push("open sidebar group is not aria-expanded=true");
    }
    if (activeRouteLink && !transparent(bg(activeRouteLink))) {
      sidebarFailures.push("closed route-active sidebar parent still has active block background");
    }
    const activeSubmenuLinks = Array.from(document.querySelectorAll("[data-om-sidebar] .oldman-submenu-link.active")).filter(visible);
    for (const link of activeSubmenuLinks) {
      if (!transparent(bg(link))) sidebarFailures.push("active sidebar submenu link uses a full block background");
    }
    return sidebarFailures;
  };

  const cards = Array.from(document.querySelectorAll(".om-card")).filter(visible);
  if (cards.length < 6) failures.push(`expected at least 6 visible om-card elements, got ${cards.length}`);
  for (const card of cards.slice(0, 3)) {
    const style = getComputedStyle(card);
    if (transparent(style.backgroundColor)) failures.push("om-card background is transparent");
    if (parseFloat(style.borderRadius) <= 0) failures.push("om-card border radius is missing");
  }
  for (const card of cards.slice(0, 8)) {
    const rect = card.getBoundingClientRect();
    if (rect.width < 180) failures.push(`dashboard card is too narrow: ${Math.round(rect.width)}px`);
  }
  const overlap = (a, b) => {
    const left = Math.max(a.left, b.left);
    const right = Math.min(a.right, b.right);
    const top = Math.max(a.top, b.top);
    const bottom = Math.min(a.bottom, b.bottom);
    return Math.max(0, right - left) * Math.max(0, bottom - top);
  };
  const cardRects = cards.slice(0, 8).map((card) => card.getBoundingClientRect());
  for (let i = 0; i < cardRects.length; i += 1) {
    for (let j = i + 1; j < cardRects.length; j += 1) {
      if (overlap(cardRects[i], cardRects[j]) > 4) failures.push("dashboard cards overlap");
    }
  }

  const table = document.querySelector(".om-table");
  if (!visible(table)) failures.push("dashboard om-table is not visible");
  if (document.documentElement.scrollWidth > window.innerWidth + 2) failures.push(`desktop has horizontal overflow ${document.documentElement.scrollWidth} > ${window.innerWidth}`);
  failures.push(...(await topbarInteractionFailures()));
  failures.push(...(await sidebarStateFailures()));

  const closedToggle = Array.from(document.querySelectorAll("[data-om-menu-toggle]")).find((toggle) => toggle.getAttribute("aria-expanded") === "false");
  if (!closedToggle) {
    failures.push("no closed sidebar toggle found");
  } else {
    const panel = document.querySelector(`#${CSS.escape(closedToggle.getAttribute("aria-controls"))}`);
    closedToggle.click();
    await sleep(150);
    if (closedToggle.getAttribute("aria-expanded") !== "true") failures.push("sidebar toggle did not set aria-expanded=true");
    if (!visible(panel) || panel.hidden || panel.classList.contains("hidden") || !panel.classList.contains("show")) {
      failures.push("sidebar panel did not open visibly");
    }
  }

  const notificationToggle = document.querySelector("#page-header-notifications-dropdown");
  const notificationMenu = document.querySelector("#notificationDropdown [data-om-dropdown-menu]");
  if (!notificationToggle || !notificationMenu) {
    failures.push("missing notification dropdown");
  } else {
    notificationToggle.click();
    await sleep(120);
    if (!visible(notificationMenu) || notificationMenu.hidden || notificationMenu.classList.contains("hidden")) failures.push("notification dropdown did not open");
    document.body.click();
    await sleep(120);
    if (visible(notificationMenu)) failures.push("notification dropdown did not close on outside click");
  }

  const userToggle = document.querySelector("#page-header-user-dropdown");
  const userMenu = userToggle?.closest("[data-om-component='dropdown'], .om-dropdown")?.querySelector("[data-om-dropdown-menu]");
  if (!userToggle || !userMenu) {
    failures.push("missing user dropdown");
  } else {
    userToggle.click();
    await sleep(120);
    if (!visible(userMenu) || userMenu.hidden || userMenu.classList.contains("hidden")) failures.push("user dropdown did not open");
    document.body.click();
    await sleep(120);
    if (visible(userMenu)) failures.push("user dropdown did not close on outside click");
  }

  return { failures };
})()
""",
        timeout=10,
    ) or {}


def assert_dashboard_chart_loading_overlay_3g(port: int, base_url: str, result: VerificationResult) -> None:
    """Verify 3G chart loading in an isolated page target."""
    client = CDPClient(create_page_websocket(port), result)
    target_id: str | None = None
    try:
        for domain in ("Page", "Runtime", "Network", "Log"):
            client.command(f"{domain}.enable")
        configure_viewport(client, 1440, 1000, mobile=False)
        target_info = client.command("Target.getTargetInfo").get("targetInfo", {})
        target_id = target_info.get("targetId")
        login(
            client,
            base_url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )
        _assert_dashboard_chart_loading_overlay_3g(client, base_url, result)
    finally:
        if target_id:
            try:
                client.command("Target.closeTarget", {"targetId": target_id})
            except Exception:
                pass
        client.close()


def _assert_dashboard_chart_loading_overlay_3g(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """Verify dashboard chart cards show scoped loading overlays under slow network."""
    script_identifier: str | None = None
    try:
        client.command(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 400,
                "downloadThroughput": 50 * 1024,
                "uploadThroughput": 20 * 1024,
            },
        )
        injected = client.command(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": r"""
(() => {
  const chartPaths = [
    "/dashboard/charts/programme-trend",
    "/dashboard/charts/feed-status",
    "/dashboard/charts/logo-quality",
  ];
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__oldmanChartUrl = String(url || "");
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    if (chartPaths.some((path) => this.__oldmanChartUrl.includes(path))) {
      setTimeout(() => originalSend.apply(this, args), 5000);
      return;
    }
    return originalSend.apply(this, args);
  };
  window.__oldmanRestoreChartDelay = () => {
    XMLHttpRequest.prototype.open = originalOpen;
    XMLHttpRequest.prototype.send = originalSend;
    delete window.__oldmanRestoreChartDelay;
  };
})();
""",
            },
        )
        script_identifier = injected.get("identifier")
        analytics_url = urllib.parse.urljoin(base_url, "/dashboard/analytics")
        client.load_seen = False
        navigation_result = client.command("Page.navigate", {"url": analytics_url})
        if error_text := navigation_result.get("errorText"):
            raise VerificationError(f"dashboard chart 3g Page.navigate failed: {error_text}")
        client.pump(0.5)

        deadline = time.monotonic() + 8.0
        last_state: dict[str, object] = {}
        while time.monotonic() < deadline:
            state = client.evaluate(
                r"""
(() => {
  const failures = [];
  const fullscreenPreloader = document.querySelector("#preloader");
  const chartRoots = Array.from(document.querySelectorAll("[data-om-component='apex-chart']"));
  const loadingScopes = Array.from(document.querySelectorAll("[data-om-loading-initial='true']")).filter((scope) => scope.querySelector("[data-om-component='apex-chart']"));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const fullscreenVisible = visible(fullscreenPreloader);
  if (chartRoots.length < 3) failures.push(`expected 3 dashboard chart roots, found ${chartRoots.length}`);
  if (loadingScopes.length < 3) failures.push(`expected 3 dashboard chart loading scopes, found ${loadingScopes.length}`);
  for (const scope of loadingScopes) {
    const root = scope.querySelector("[data-om-component='apex-chart']");
    const src = root?.getAttribute("data-om-chart-src") || "(unknown)";
    const overlay = scope.querySelector(":scope > [data-om-scoped-preloader]");
    const legacyLoading = root?.querySelector("[data-om-chart-loading]");
    if (!visible(overlay)) failures.push(`${src} scoped card loading overlay is not visible during 3g delay`);
    if (scope.dataset.omPreloaderStatus !== "loading") failures.push(`${src} card preloader status is ${scope.dataset.omPreloaderStatus || "(empty)"}`);
    if (legacyLoading && !legacyLoading.hidden) failures.push(`${src} legacy text loading is visible`);
    if (overlay && getComputedStyle(overlay).position !== "absolute") failures.push(`${src} card loading overlay is not absolutely positioned`);
  }
  return {
    failures,
    fullscreenVisible,
    chartCount: chartRoots.length,
    loadingScopeCount: loadingScopes.length,
    loadingCount: loadingScopes.filter((scope) => scope.dataset.omPreloaderStatus === "loading").length,
    overlayCount: loadingScopes.filter((scope) => visible(scope.querySelector(":scope > [data-om-scoped-preloader]"))).length,
  };
})()
""",
                timeout=5.0,
            ) or {}
            last_state = state
            state_failures = assertions("dashboard chart 3g loading", state)
            if (
                not state.get("fullscreenVisible")
                and not state_failures
                and int(state.get("loadingCount", 0)) >= 3
                and int(state.get("overlayCount", 0)) >= 3
            ):
                save_screenshot(client, CHART_LOADING_3G_SCREENSHOT)
                result.screenshots["tailwind-dashboard-chart-loading-3g"] = CHART_LOADING_3G_SCREENSHOT
                break
            time.sleep(0.1)
        else:
            failures = last_state.get("failures")
            if isinstance(failures, list):
                result.pageErrors.extend(f"dashboard chart 3g loading: {failure}" for failure in failures)
            result.pageErrors.append(f"dashboard chart 3g loading: 未观察到 3 个图表遮罩，状态 {last_state}")
    finally:
        try:
            client.evaluate("window.__oldmanRestoreChartDelay?.()", timeout=2.0)
        except Exception:
            pass
        if script_identifier:
            try:
                client.command("Page.removeScriptToEvaluateOnNewDocument", {"identifier": script_identifier})
            except Exception:
                pass
        try:
            client.command(
                "Network.emulateNetworkConditions",
                {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
            )
        except Exception:
            pass


def users_interactions_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const openState = (modal) => modal && !modal.hidden && modal.getAttribute("aria-hidden") !== "true" && modal.classList.contains("is-open");
  const legacyClassTokens = () => {
    const banned = new Set([
      "container-fluid", "row", "d-sm-flex", "d-lg-flex", "bg-light", "bg-light-subtle", "text-muted",
      "img-fluid", "h-100", "w-100", "me-1", "me-2", "me-3", "ms-1", "ms-2", "ms-3",
      "breadcrumb", "breadcrumb-item", "alert", "btn", "form-label", "text-uppercase",
      "material-shadow-none", "position-absolute", "position-relative", "text-reset",
      "text-decoration-underline", "text-decoration-none"
    ]);
    const matches = [];
    for (const element of document.querySelectorAll("[class]")) {
      for (const token of String(element.getAttribute("class") || "").split(/\s+/).filter(Boolean)) {
        if (banned.has(token) || /^col(-(sm|md|lg|xl|xxl))?-\d+$/.test(token) || /^fs-\d+$/.test(token)) {
          matches.push(token);
        }
      }
    }
    return [...new Set(matches)].sort();
  };
  const waitForClosed = async (modal) => {
    for (let i = 0; i < 12; i += 1) {
      if (!openState(modal) && !visible(modal) && !document.querySelector(".om-modal-backdrop")) return true;
      await sleep(75);
    }
    return false;
  };

  for (let i = 0; i < 60 && !document.querySelector("[data-om-table-row], .om-table tbody tr"); i += 1) await sleep(100);
  const tableRoot = document.querySelector("[data-om-component='table']");
  const legacyTokens = legacyClassTokens();
  if (legacyTokens.length) failures.push(`legacy Bootstrap class tokens remain: ${legacyTokens.join(", ")}`);
  if (!tableRoot) failures.push("missing users table component");
  const table = document.querySelector(".om-table");
  if (!visible(table)) failures.push("users om-table is not visible");

  const pageSize = document.querySelector("[data-om-table-page-size-control]");
  if (!visible(pageSize)) failures.push("missing visible page-size control");
  const assertTableLoadingOverlay = async () => {
    if (!tableRoot) return;
    const pageButton = tableRoot.querySelector('[data-om-table-page="2"]');
    if (!pageButton) return;
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.__oldmanTableUrl = String(url || "");
      return originalOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function(...args) {
      if (this.__oldmanTableUrl && this.__oldmanTableUrl.includes("/users/table")) {
        setTimeout(() => originalSend.apply(this, args), 350);
        return;
      }
      return originalSend.apply(this, args);
    };
    try {
      pageButton.click();
      await sleep(50);
      const overlay = tableRoot.querySelector("[data-om-scoped-preloader]");
      const loadingScope = overlay?.closest("[data-om-preloader-status]") || tableRoot.querySelector(".om-table-shell[data-om-preloader-status]") || tableRoot;
      const legacyLoading = tableRoot.querySelector("[data-om-table-loading]");
      if (!visible(overlay)) failures.push("users table page change did not show scoped loading overlay");
      if (loadingScope.dataset.omPreloaderStatus !== "loading") failures.push(`users table preloader status is ${loadingScope.dataset.omPreloaderStatus || "(empty)"}`);
      if (legacyLoading && !legacyLoading.hidden) failures.push("users table legacy loading text is visible under overlay");
      if (overlay && getComputedStyle(overlay).position !== "absolute") failures.push("users table loading overlay is not absolutely positioned");
      for (let i = 0; i < 30 && tableRoot.dataset.omStatus === "loading"; i += 1) await sleep(50);
      if (tableRoot.querySelector("[data-om-scoped-preloader]")) failures.push("users table loading overlay remains after refresh");
    } finally {
      XMLHttpRequest.prototype.open = originalOpen;
      XMLHttpRequest.prototype.send = originalSend;
    }
  };
  await assertTableLoadingOverlay();
  const openRowActionModal = async (target, label) => {
    const firstRow = document.querySelector("[data-om-table-row], .om-table tbody tr");
    const actionToggle = firstRow?.querySelector("[data-om-dropdown-toggle]");
    if (!actionToggle) {
      failures.push(`missing row action dropdown toggle for ${label}`);
      return;
    }
    const menu = actionToggle.closest(".om-dropdown")?.querySelector("[data-om-dropdown-menu], .om-dropdown-menu");
    actionToggle.click();
    await sleep(150);
    if (!visible(menu) || menu.hidden || menu.classList.contains("hidden")) {
      failures.push(`row action dropdown did not open for ${label}`);
      return;
    }
    const modalButton = menu?.querySelector(`[data-om-modal-target="${target}"]`);
    if (!modalButton) {
      failures.push(`missing ${label} modal action`);
      return;
    }
    modalButton.click();
    await sleep(500);
    const modal = document.querySelector(target);
    if (!visible(modal) || !modal.classList.contains("is-open")) failures.push(`${label} modal did not open`);
    if (!modal?.querySelector("form")) failures.push(`${label} modal did not load form content`);
    modal?.querySelector("[data-om-modal-close]")?.click();
    if (!(await waitForClosed(modal))) failures.push(`${label} modal did not close`);
  };
  await openRowActionModal("#user-password-modal", "user password");
  await openRowActionModal("#user-status-modal", "user status");
  await openRowActionModal("#user-delete-modal", "user delete");
  return { failures };
})()
""",
        timeout=12,
    ) or {}


def modal_interactions_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };

  const trigger = document.querySelector('[data-om-modal-target="#upstream-records-help"]');
  const legacyClassTokens = () => {
    const banned = new Set([
      "container-fluid", "row", "d-sm-flex", "d-lg-flex", "bg-light", "bg-light-subtle", "text-muted",
      "img-fluid", "h-100", "w-100", "me-1", "me-2", "me-3", "ms-1", "ms-2", "ms-3",
      "breadcrumb", "breadcrumb-item", "alert", "btn", "form-label", "text-uppercase",
      "material-shadow-none", "position-absolute", "position-relative", "text-reset",
      "text-decoration-underline", "text-decoration-none"
    ]);
    const matches = [];
    for (const element of document.querySelectorAll("[class]")) {
      for (const token of String(element.getAttribute("class") || "").split(/\s+/).filter(Boolean)) {
        if (banned.has(token) || /^col(-(sm|md|lg|xl|xxl))?-\d+$/.test(token) || /^fs-\d+$/.test(token)) {
          matches.push(token);
        }
      }
    }
    return [...new Set(matches)].sort();
  };
  const legacyTokens = legacyClassTokens();
  if (legacyTokens.length) failures.push(`legacy Bootstrap class tokens remain: ${legacyTokens.join(", ")}`);
  const openState = (modal) => modal && !modal.hidden && modal.getAttribute("aria-hidden") !== "true" && modal.classList.contains("is-open");
  const waitForClosed = async (modal) => {
    for (let i = 0; i < 12; i += 1) {
      if (!openState(modal) && !visible(modal) && !document.querySelector(".om-modal-backdrop")) return true;
      await sleep(75);
    }
    return false;
  };
  if (!trigger) {
    failures.push("missing upstream help modal trigger");
  } else {
    trigger.click();
    await sleep(200);
    const modal = document.querySelector("#upstream-records-help");
    const surface = modal?.querySelector(".om-modal-surface");
    if (!visible(modal) || !openState(modal)) failures.push("static modal did not open");
    if (!visible(surface)) failures.push("static modal surface is not visible");
    if (surface) {
      const rect = surface.getBoundingClientRect();
      const centerDelta = Math.abs((rect.left + rect.width / 2) - window.innerWidth / 2);
      if (centerDelta > 24) failures.push(`static modal surface is not centered: ${Math.round(centerDelta)}px off`);
    }
    if (!document.querySelector(".om-modal-backdrop")) failures.push("modal backdrop missing");
  }
  return { failures };
})()
""",
        timeout=8,
    ) or {}


def modal_close_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const modal = document.querySelector("#upstream-records-help");
  const openState = (target) => target && !target.hidden && target.getAttribute("aria-hidden") !== "true" && target.classList.contains("is-open");
  modal?.querySelector("[data-om-modal-close]")?.click();
  for (let i = 0; i < 12; i += 1) {
    if (!openState(modal) && !visible(modal) && !document.querySelector(".om-modal-backdrop")) return { failures };
    await sleep(75);
  }
  failures.push("static modal did not close");
  return { failures };
})()
""",
        timeout=8,
    ) or {}


def mobile_dashboard_js(client: CDPClient) -> dict[str, object]:
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };

  const legacyClassTokens = () => {
    const banned = new Set([
      "container-fluid", "row", "d-sm-flex", "d-lg-flex", "bg-light", "bg-light-subtle", "text-muted",
      "img-fluid", "h-100", "w-100", "me-1", "me-2", "me-3", "ms-1", "ms-2", "ms-3",
      "breadcrumb", "breadcrumb-item", "alert", "btn", "form-label", "text-uppercase",
      "material-shadow-none", "position-absolute", "position-relative", "text-reset",
      "text-decoration-underline", "text-decoration-none"
    ]);
    const matches = [];
    for (const element of document.querySelectorAll("[class]")) {
      for (const token of String(element.getAttribute("class") || "").split(/\s+/).filter(Boolean)) {
        if (banned.has(token) || /^col(-(sm|md|lg|xl|xxl))?-\d+$/.test(token) || /^fs-\d+$/.test(token)) {
          matches.push(token);
        }
      }
    }
    return [...new Set(matches)].sort();
  };
  const legacyTokens = legacyClassTokens();
  if (legacyTokens.length) failures.push(`legacy Bootstrap class tokens remain: ${legacyTokens.join(", ")}`);
  if (document.documentElement.scrollWidth > window.innerWidth + 2) failures.push(`mobile has horizontal overflow ${document.documentElement.scrollWidth} > ${window.innerWidth}`);
  const sidebar = document.querySelector("[data-om-sidebar]");
  const toggle = document.querySelector("[data-om-sidebar-toggle]");
  const backdrop = document.querySelector("[data-om-sidebar-backdrop]");
  if (!sidebar || !toggle) {
    failures.push("missing mobile sidebar or toggle");
  } else {
    if (sidebar.getBoundingClientRect().right > 2) failures.push("mobile sidebar is visible before toggle");
    toggle.click();
    await sleep(250);
    if (document.documentElement.dataset.omSidebarOpen !== "true") failures.push("mobile sidebar did not set open state");
    if (sidebar.getBoundingClientRect().right < window.innerWidth * 0.5) failures.push("mobile sidebar did not slide into viewport");
    if (backdrop && (backdrop.hidden || backdrop.classList.contains("hidden"))) failures.push("mobile sidebar backdrop did not show");
    backdrop?.click();
    await sleep(250);
    if (document.documentElement.dataset.omSidebarOpen === "true") failures.push("mobile backdrop did not close sidebar");
  }

  const cards = Array.from(document.querySelectorAll(".om-card")).filter(visible);
  if (cards.length < 2) failures.push(`mobile expected visible cards, got ${cards.length}`);
  return { failures };
})()
""",
        timeout=10,
    ) or {}


if __name__ == "__main__":
    raise SystemExit(main())
