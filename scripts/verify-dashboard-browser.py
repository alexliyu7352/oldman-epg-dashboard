#!/usr/bin/env python3
"""Verify the Oldman dashboard in a real Chrome browser via CDP."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
while str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_URL = "http://localhost:17998/"
SCREENSHOT_ROOT = Path(os.environ.get("OLDMAN_EPG_CHILD_SCREENSHOT_DIR", "/tmp")).expanduser().resolve()
DESKTOP_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-dashboard-desktop.png")
MOBILE_SCREENSHOT = str(SCREENSHOT_ROOT / "oldman-dashboard-mobile.png")
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"


class VerificationError(RuntimeError):
    """Raised when the browser verification cannot complete successfully."""


class PageLoadTimeout(VerificationError):
    """Raised only when Chrome does not emit Page.loadEventFired before the deadline."""


def same_origin_path_query(actual_url: str, expected_url: str) -> bool:
    """Compare the origin, path, and query portions of two browser URLs."""
    actual = urllib.parse.urlsplit(actual_url)
    expected = urllib.parse.urlsplit(expected_url)
    return (
        actual.scheme.lower(),
        actual.netloc.lower(),
        actual.path or "/",
        actual.query,
    ) == (
        expected.scheme.lower(),
        expected.netloc.lower(),
        expected.path or "/",
        expected.query,
    )


def assertion_failures(payload: object) -> list[str]:
    """Validate and return one browser assertion payload's failures."""
    if not isinstance(payload, dict):
        raise VerificationError("Browser assertion payload must be an object")
    if "failures" not in payload:
        raise VerificationError("Browser assertion payload is missing failures")
    failures = payload["failures"]
    if not isinstance(failures, list):
        raise VerificationError("Browser assertion payload failures must be a list")

    validated: list[str] = []
    for failure in failures:
        if not isinstance(failure, str) or not failure.strip():
            raise VerificationError("Browser assertion payload failures must contain non-empty strings")
        validated.append(failure)
    return validated


def required_success_string(payload: dict[str, Any], field_name: str, context: str) -> str:
    """Return a required non-empty string from a successful browser assertion."""
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"{context} success payload requires non-empty string {field_name}")
    return value


def required_success_bool(payload: dict[str, Any], field_name: str, context: str) -> bool:
    """Return a required boolean from a successful browser assertion."""
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise VerificationError(f"{context} success payload requires boolean {field_name}")
    return value


def required_canonical_uuid(payload: dict[str, Any], field_name: str, context: str) -> str:
    """Return a required canonical UUID string from a successful assertion."""
    value = required_success_string(payload, field_name, context)
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise VerificationError(f"{context} success payload requires canonical UUID {field_name}") from error
    if str(parsed) != value:
        raise VerificationError(f"{context} success payload requires canonical UUID {field_name}")
    return value


def required_positive_integer_string(payload: dict[str, Any], field_name: str, context: str) -> str:
    """Return a required positive base-10 integer encoded as a string."""
    value = required_success_string(payload, field_name, context)
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise VerificationError(f"{context} success payload requires positive integer string {field_name}")
    return value


def required_success_route(payload: dict[str, Any], field_name: str, pattern: str, context: str) -> str:
    """Return a required route matching the complete expected path pattern."""
    value = required_success_string(payload, field_name, context)
    if re.fullmatch(pattern, value) is None:
        raise VerificationError(f"{context} success payload {field_name} does not match {pattern}")
    return value


TURBO_COUNT_FIELDS = {
    "beforeFetch",
    "beforeRender",
    "render",
    "load",
    "beforeFrameRender",
    "frameRender",
    "frameLoad",
}
PERFORMANCE_NAVIGATION_TYPES = {"navigate", "reload", "back_forward", "prerender"}
BROWSER_BACK_FRAME_STATES = {"loading", "rendering", "mounting", "mounted", "failed"}
BROWSER_BACK_PRELOADER_STATUSES = {"loading", "idle"}


def required_payload_object(payload: object, context: str) -> dict[str, Any]:
    """Return a browser probe payload only when its root is an object."""
    if not isinstance(payload, dict):
        raise VerificationError(f"{context} payload must be an object")
    return payload


def required_non_negative_integer(payload: dict[str, Any], field_name: str, context: str) -> int:
    """Return a required non-negative integer while rejecting booleans."""
    value = payload.get(field_name)
    if type(value) is not int or value < 0:
        raise VerificationError(f"{context} payload requires non-negative integer {field_name}")
    return value


def required_string_field(payload: dict[str, Any], field_name: str, context: str) -> str:
    """Return a required string field while allowing an explicitly empty value."""
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise VerificationError(f"{context} payload requires string {field_name}")
    return value


def required_navigation_type(payload: dict[str, Any], field_name: str, context: str) -> str:
    """Return a PerformanceNavigationTiming type from the browser-defined inventory."""
    value = required_success_string(payload, field_name, context)
    if value not in PERFORMANCE_NAVIGATION_TYPES:
        raise VerificationError(f"{context} payload has invalid {field_name}")
    return value


def required_turbo_counts(payload: dict[str, Any], context: str, *, initial: bool = False) -> dict[str, int]:
    """Validate the exact Turbo counter inventory and integer values."""
    counts = payload.get("counts")
    if not isinstance(counts, dict) or set(counts) != TURBO_COUNT_FIELDS:
        raise VerificationError(f"{context} payload has invalid Turbo count fields")
    validated: dict[str, int] = {}
    for field_name in TURBO_COUNT_FIELDS:
        value = counts[field_name]
        if type(value) is not int or value < 0:
            raise VerificationError(f"{context} payload requires non-negative integer count {field_name}")
        if initial and value != 0:
            raise VerificationError(f"{context} initial Turbo count {field_name} must be zero")
        validated[field_name] = value
    return validated


def required_browser_back_state(payload: object, context: str) -> dict[str, Any]:
    """Validate one browser-back frame and preloader state payload."""
    state = required_payload_object(payload, context)
    path = required_success_string(state, "path", context)
    if not path.startswith("/"):
        raise VerificationError(f"{context} payload path must be absolute")
    required_canonical_uuid(state, "probe", context)
    required_non_negative_integer(state, "navigationCount", context)
    frame_state = required_string_field(state, "mainFrameState", context)
    if frame_state not in BROWSER_BACK_FRAME_STATES:
        raise VerificationError(f"{context} payload has invalid mainFrameState")
    preloader_status = required_string_field(state, "mainFramePreloaderStatus", context)
    if preloader_status not in BROWSER_BACK_PRELOADER_STATUSES:
        raise VerificationError(f"{context} payload has invalid mainFramePreloaderStatus")
    required_success_bool(state, "hasScopedPreloader", context)
    required_success_bool(state, "sawLoading", context)
    required_success_bool(state, "ready", context)
    return state


def required_request_records(payload: dict[str, Any], context: str) -> list[dict[str, Any]]:
    """Validate request-probe records before Python recomputes a match."""
    requests = payload.get("requests")
    if not isinstance(requests, list):
        raise VerificationError(f"{context} payload requests must be a list")
    validated: list[dict[str, Any]] = []
    for index, request in enumerate(requests):
        item_context = f"{context} request[{index}]"
        if not isinstance(request, dict) or set(request) != {"type", "url", "method", "headers"}:
            raise VerificationError(f"{item_context} has invalid fields")
        request_type = required_success_string(request, "type", item_context)
        if request_type not in {"fetch", "xhr"}:
            raise VerificationError(f"{item_context} type must be fetch or xhr")
        required_success_string(request, "url", item_context)
        required_success_string(request, "method", item_context)
        headers = request.get("headers")
        if not isinstance(headers, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
            raise VerificationError(f"{item_context} headers must map strings to strings")
        validated.append(request)
    return validated


def required_request_probe_identity(payload: dict[str, Any], context: str) -> tuple[str, str, str, int]:
    """Validate the page identity shared by request-probe snapshots."""
    path = required_success_string(payload, "path", context)
    if not path.startswith("/"):
        raise VerificationError(f"{context} payload path must be absolute")
    origin = required_success_string(payload, "origin", context)
    probe = required_canonical_uuid(payload, "probe", context)
    navigation_count = required_non_negative_integer(payload, "navigationCount", context)
    return path, origin, probe, navigation_count


def has_matching_request(
    requests: list[dict[str, Any]],
    origin: str,
    path: str,
    *,
    method: str,
    accept_contains: str | None = None,
) -> bool:
    """Recompute a same-origin request match from validated browser records."""
    for request in requests:
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(f"{origin}/", request["url"]))
        request_origin = f"{parsed.scheme}://{parsed.netloc}"
        if request_origin != origin or parsed.path != path or request["method"].upper() != method:
            continue
        if accept_contains is not None and accept_contains not in request["headers"].get("accept", ""):
            continue
        return True
    return False


@dataclass
class VerificationResult:
    """Collects the structured result fields required by the CLI contract."""

    ok: bool = False
    consoleErrors: list[str] = field(default_factory=list)
    pageErrors: list[str] = field(default_factory=list)
    badResponses: list[dict[str, Any]] = field(default_factory=list)
    desktopScreenshot: str = DESKTOP_SCREENSHOT
    mobileScreenshot: str = MOBILE_SCREENSHOT
    screenshots: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        """Return a JSON-serializable view of the verification result."""
        return {
            "ok": self.ok,
            "consoleErrors": self.consoleErrors,
            "pageErrors": self.pageErrors,
            "badResponses": self.badResponses,
            "desktopScreenshot": self.desktopScreenshot,
            "mobileScreenshot": self.mobileScreenshot,
            "screenshots": self.screenshots,
        }


class WebSocket:
    """Minimal RFC 6455 client sufficient for local Chrome DevTools traffic."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        """Open a websocket connection and perform the HTTP upgrade handshake."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise VerificationError(f"Unsupported DevTools websocket scheme: {parsed.scheme}")

        self.sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        host = parsed.netloc
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._read_http_response()
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise VerificationError(f"Chrome DevTools websocket handshake failed: {response.splitlines()[0]}")

    def close(self) -> None:
        """Close the underlying socket."""
        try:
            self.sock.close()
        except OSError:
            pass

    def send_json(self, payload: dict[str, Any]) -> None:
        """Serialize and send one masked text frame to Chrome."""
        self._send_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"), opcode=0x1)

    def recv_json(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Receive one JSON message, returning None when the timeout expires."""
        previous_timeout = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                opcode, payload = self._recv_frame()
                if opcode == 0x1:
                    return json.loads(payload.decode("utf-8"))
                if opcode == 0x8:
                    return None
                if opcode == 0x9:
                    self._send_frame(payload, opcode=0xA)
        except TimeoutError:
            return None
        finally:
            self.sock.settimeout(previous_timeout)

    def _read_http_response(self) -> str:
        """Read the websocket upgrade response headers."""
        chunks: list[bytes] = []
        while b"\r\n\r\n" not in b"".join(chunks):
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("iso-8859-1", errors="replace")

    def _send_frame(self, payload: bytes, opcode: int) -> None:
        """Write a single masked websocket frame."""
        first = 0x80 | opcode
        length = len(payload)
        mask_bit = 0x80
        if length < 126:
            header = struct.pack("!BB", first, mask_bit | length)
        elif length < 65536:
            header = struct.pack("!BBH", first, mask_bit | 126, length)
        else:
            header = struct.pack("!BBQ", first, mask_bit | 127, length)

        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        """Read a single websocket frame from Chrome."""
        first_two = self._recv_exact(2)
        first, second = first_two[0], first_two[1]
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]

        masked = bool(second & 0x80)
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, size: int) -> bytes:
        """Read exactly size bytes or fail if the socket closes."""
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise VerificationError("Chrome DevTools websocket closed unexpectedly")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class CDPClient:
    """Small Chrome DevTools Protocol client for page verification."""

    def __init__(self, websocket_url: str, result: VerificationResult) -> None:
        """Create a CDP client bound to one page target."""
        self.ws = WebSocket(websocket_url)
        self.result = result
        self.next_id = 1
        self.load_seen = False
        self.request_methods: dict[str, str] = {}

    def close(self) -> None:
        """Close the DevTools websocket."""
        self.ws.close()

    def command(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        """Send a CDP command and return its response while collecting events."""
        message_id = self.next_id
        self.next_id += 1
        self.ws.send_json({"id": message_id, "method": method, "params": params or {}})

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.ws.recv_json(timeout=max(0.05, deadline - time.monotonic()))
            if message is None:
                continue
            if "method" in message:
                self._handle_event(message)
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise VerificationError(f"CDP command {method} failed: {message['error']}")
                return message.get("result", {})
        raise VerificationError(f"Timed out waiting for CDP command: {method}")

    def pump(self, seconds: float) -> None:
        """Collect asynchronous CDP events for a short fixed interval."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            message = self.ws.recv_json(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            if message and "method" in message:
                self._handle_event(message)

    def wait_for_load(self, timeout: float = 15.0) -> None:
        """Wait until Chrome reports the page load event."""
        deadline = time.monotonic() + timeout
        while not self.load_seen and time.monotonic() < deadline:
            message = self.ws.recv_json(timeout=max(0.05, deadline - time.monotonic()))
            if message and "method" in message:
                self._handle_event(message)
        if not self.load_seen:
            raise PageLoadTimeout("Timed out waiting for page load event")

    def evaluate(self, expression: str, timeout: float = 10.0) -> Any:
        """Evaluate JavaScript in the page and return a JSON-compatible value."""
        caller = sys._getframe(1)
        parent = caller.f_back
        context = f"{caller.f_code.co_name}:{caller.f_lineno}"
        if parent is not None:
            context = f"{parent.f_code.co_name}:{parent.f_lineno} -> {context}"
        try:
            response = self.command(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                    "userGesture": True,
                },
                timeout=timeout,
            )
        except VerificationError as error:
            raise VerificationError(f"{error} ({context})") from error
        if "exceptionDetails" in response:
            details = response["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text") or "Runtime.evaluate failed"
            raise VerificationError(text)
        return response.get("result", {}).get("value")

    def _handle_event(self, message: dict[str, Any]) -> None:
        """Collect console, page, and network failure events from CDP."""
        method = message.get("method")
        params = message.get("params", {})

        if method == "Page.loadEventFired":
            self.load_seen = True
            return

        if method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            self.result.consoleErrors.append(format_console_args(params.get("args", [])))
            return

        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            exception = details.get("exception", {})
            description = exception.get("description") or exception.get("value")
            text = details.get("text")
            stack = details.get("stackTrace", {}).get("callFrames", [])
            location = ""
            if stack:
                frame = stack[0]
                location = f" at {frame.get('url', '')}:{frame.get('lineNumber', 0) + 1}:{frame.get('columnNumber', 0) + 1}"
            self.result.pageErrors.append(str(description or text or "Unhandled page exception") + location)
            return

        if method == "Log.entryAdded":
            entry = params.get("entry", {})
            if entry.get("level") == "error":
                text = entry.get("text") or "Chrome log error"
                if "422 (Unprocessable Entity)" not in text and "400 (Bad Request)" not in text:
                    if entry.get("url"):
                        text = f"{text} ({entry.get('url')})"
                    self.result.consoleErrors.append(text)
            return

        if method == "Network.requestWillBeSent":
            request_id = str(params.get("requestId") or "")
            request_method = str(params.get("request", {}).get("method") or "").upper()
            if request_id and request_method:
                self.request_methods[request_id] = request_method
            return

        if method == "Network.responseReceived":
            response = params.get("response", {})
            request_id = str(params.get("requestId") or "")
            if request_id and self.request_methods.get(request_id):
                response = dict(response)
                request_headers = dict(response.get("requestHeaders") or {})
                request_headers.setdefault("method", self.request_methods[request_id])
                response["requestHeaders"] = request_headers
            status = int(response.get("status", 0))
            if status >= 400 and not is_expected_form_validation_response(response):
                self.result.badResponses.append({"url": response.get("url"), "status": status})
            return

        if method == "Network.loadingFailed" and not params.get("canceled"):
            error = str(params.get("errorText") or "")
            if error and error != "net::ERR_ABORTED":
                self.result.badResponses.append({"error": error, "requestId": params.get("requestId")})


def format_console_args(args: list[dict[str, Any]]) -> str:
    """Convert Runtime.consoleAPICalled arguments into a readable error string."""
    parts: list[str] = []
    for arg in args:
        value = arg.get("value")
        if value is not None:
            parts.append(str(value))
        else:
            parts.append(arg.get("description") or arg.get("type") or "<unknown>")
    return " ".join(parts) if parts else "console.error"


def is_expected_form_validation_response(response: dict[str, Any]) -> bool:
    """判断网络错误是否属于浏览器门禁主动触发的表单校验 422。"""
    url = response.get("url") or ""
    method = str(response.get("requestHeaders", {}).get(":method") or response.get("requestHeaders", {}).get("method") or "").upper()
    path = urllib.parse.urlparse(url).path
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    status = int(response.get("status", 0))

    expected_dashboard_chart_paths = {
        "/dashboard/charts/programme-trend",
        "/dashboard/charts/feed-status",
        "/dashboard/charts/logo-quality",
    }
    if status == 400 and method == "GET" and path in expected_dashboard_chart_paths and query.get("range") == ["invalid"]:
        return True

    if status != 422 or method != "POST":
        return False

    return bool(
        re.fullmatch(r"/channels-epg/(new|\d+/edit)", path)
        or re.fullmatch(r"/channel-names/(new|\d+/edit)", path)
        or re.fullmatch(r"/epg-list/(new|\d+/edit)", path)
        or re.fullmatch(r"/catalog-channels/(new|\d+/edit)", path)
        or re.fullmatch(r"/catalog-feeds/(new|\d+/edit)", path)
        or re.fullmatch(r"/match-decisions/\d+/edit", path)
        or re.fullmatch(r"/users/\d+/(password|status|delete)", path)
        or path == "/user-session/password"
    )


def find_chrome() -> str:
    """Find an installed Chrome or Chromium executable."""
    candidates = [
        os.environ.get("CHROME_BIN"),
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ]
    for candidate in candidates:
        if candidate:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    raise VerificationError("No Chrome/Chromium executable found; install Chrome or set CHROME_BIN")


def find_free_port() -> int:
    """Reserve and return an available localhost TCP port for Chrome DevTools."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_devtools(port: int, timeout: float = 10.0) -> str:
    """Poll Chrome's /json/version endpoint until the page websocket is available."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as sock:
                request = f"GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
                sock.sendall(request.encode("ascii"))
                response = read_http_response(sock)
            body = response.split(b"\r\n\r\n", 1)[1]
            data = json.loads(body.decode("utf-8"))
            websocket_url = data.get("webSocketDebuggerUrl")
            if websocket_url:
                return websocket_url
        except (OSError, IndexError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise VerificationError(f"Timed out waiting for Chrome DevTools: {last_error}")


def devtools_json(port: int, path: str, method: str = "GET") -> dict[str, Any]:
    """Request a JSON endpoint from Chrome DevTools over local HTTP."""
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        request = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
        sock.sendall(request.encode("ascii"))
        response = read_http_response(sock)
    status = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    if " 200 " not in status:
        raise VerificationError(f"Chrome DevTools HTTP request failed: {status}")
    return json.loads(response.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def create_page_websocket(port: int) -> str:
    """Create a fresh page target and return its DevTools websocket URL."""
    target = devtools_json(port, "/json/new?about:blank", method="PUT")
    websocket_url = target.get("webSocketDebuggerUrl")
    if not websocket_url:
        raise VerificationError("Chrome did not return a page websocket URL")
    return websocket_url


def read_http_response(sock: socket.socket) -> bytes:
    """Read a complete small HTTP response from Chrome DevTools."""
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)

    headers, body = response.split(b"\r\n\r\n", 1)
    content_length = 0
    for line in headers.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.lower() == b"content-length":
            content_length = int(value.strip())
            break

    while content_length and len(body) < content_length:
        body += sock.recv(content_length - len(body))
    return headers + b"\r\n\r\n" + body


def launch_chrome(port: int, user_data_dir: str) -> subprocess.Popen[bytes]:
    """Start headless Chrome with a temporary profile and remote debugging enabled."""
    chrome = find_chrome()
    args = [
        chrome,
        "--headless=new",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-extensions",
        "--disable-gpu",
        "--disable-sync",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_chrome_devtools(chrome: subprocess.Popen[bytes], port: int, timeout: float = 25.0) -> str:
    """等待 Chrome DevTools，若 Chrome 提前退出则返回更可诊断的错误。"""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if chrome.poll() is not None:
            raise VerificationError(f"Chrome exited before DevTools became available: {chrome.returncode}")
        try:
            return wait_for_devtools(port, timeout=1.0)
        except VerificationError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise VerificationError(f"Timed out waiting for Chrome DevTools: {last_error}")


def js_assertions() -> str:
    """返回真实后台首页的桌面端断言脚本。"""
    return r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const raf = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
  const isTransparent = (value) => !value || value === "transparent" || value === "rgba(0, 0, 0, 0)";
  const isVisible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const hiddenState = (element) => ({
    hidden: element ? element.hidden : null,
    hasHiddenClass: element ? element.classList.contains("hidden") : null,
    visible: isVisible(element),
  });
  const changed = (before, after) => before.hidden !== after.hidden || before.hasHiddenClass !== after.hasHiddenClass || before.visible !== after.visible;

  for (let index = 0; index < 60 && document.documentElement.dataset.omReady !== "true"; index += 1) {
    await sleep(50);
  }
  await raf();

  const bodyBg = getComputedStyle(document.body).backgroundColor;
  if (isTransparent(bodyBg)) failures.push(`body background is transparent (${bodyBg})`);

  const card = document.querySelector(".om-card, .card");
  if (!card) {
    failures.push("missing card element");
  } else {
    const cardStyle = getComputedStyle(card);
    if (parseFloat(cardStyle.borderRadius) <= 0) failures.push(`card border radius is not positive (${cardStyle.borderRadius})`);
    if (isTransparent(cardStyle.backgroundColor)) failures.push(`card background is transparent (${cardStyle.backgroundColor})`);
  }

  const sidebar = document.querySelector(".app-menu, [data-om-sidebar]");
  if (!sidebar) {
    failures.push("missing sidebar element");
  } else {
    const width = sidebar.getBoundingClientRect().width;
    if (width <= 180) failures.push(`sidebar width must be > 180px on desktop (${width})`);
    const expandedPanel = sidebar.querySelector("[data-om-menu-panel].show, .sub-menu.show");
    if (!expandedPanel) {
      failures.push("sidebar has no expanded submenu panel on desktop");
    } else if (!Array.from(expandedPanel.querySelectorAll("a[href]")).some(isVisible)) {
      failures.push("expanded sidebar submenu has no visible links");
    }
  }

  const paddedButton = Array.from(document.querySelectorAll(".om-button, .btn, button, a[role='button']")).find((button) => {
    const style = getComputedStyle(button);
    return parseFloat(style.paddingLeft) > 0 && parseFloat(style.paddingRight) > 0 && parseFloat(style.paddingTop) > 0 && parseFloat(style.paddingBottom) > 0;
  });
  if (!paddedButton) failures.push("no button element has non-zero horizontal and vertical padding");

  const menuToggle =
    Array.from(document.querySelectorAll("[data-om-menu-toggle], .menu-link[data-bs-toggle='collapse']")).find((toggle) => {
      const selector = toggle.getAttribute("aria-controls") ? `#${CSS.escape(toggle.getAttribute("aria-controls"))}` : null;
      const panel = selector ? document.querySelector(selector) : toggle.closest(".nav-item, .menu-item")?.querySelector(".sub-menu, [data-om-menu-panel]");
      return panel && (panel.hidden || panel.classList.contains("hidden") || !panel.classList.contains("show"));
    }) || document.querySelector("[data-om-menu-toggle], .menu-link[data-bs-toggle='collapse']");
  if (!menuToggle) {
    failures.push("missing Oldman sidebar collapse toggle");
  } else {
    const panelSelector = menuToggle.getAttribute("aria-controls") ? `#${CSS.escape(menuToggle.getAttribute("aria-controls"))}` : null;
    const panel = panelSelector ? document.querySelector(panelSelector) : menuToggle.closest(".nav-item, .menu-item")?.querySelector(".sub-menu, [data-om-menu-panel]");
    menuToggle.click();
    await sleep(100);
    if (menuToggle.getAttribute("aria-expanded") !== "true") {
      failures.push("sidebar submenu toggle did not set aria-expanded=true");
    }
    if (!panel) {
      failures.push("sidebar submenu panel missing for toggle");
    } else if (panel.hidden || panel.classList.contains("hidden") || !panel.classList.contains("show") || !isVisible(panel)) {
      failures.push("sidebar submenu panel did not open visibly");
    }
  }

  const dropdownToggle = Array.from(document.querySelectorAll("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")).find(isVisible);
  if (!dropdownToggle) {
    failures.push("missing dropdown toggle");
  } else {
    const dropdown = dropdownToggle.closest("[data-om-component='dropdown'], .oldman-dropdown, .dropdown");
    const menu = dropdown?.querySelector("[data-om-dropdown-menu], .dropdown-menu");
    dropdownToggle.click();
    await sleep(100);
    if (!isVisible(menu)) failures.push("dropdown menu did not open");
    document.body.click();
    await sleep(100);
    if (isVisible(menu)) failures.push("dropdown menu did not close after outside click");
  }

  if (!location.pathname.includes("/dashboard") && location.pathname !== "/") failures.push(`not on dashboard page: ${location.pathname}`);
  for (const text of ["Channels", "Programmes", "Catalog Channels", "Catalog Feeds", "Logo Coverage", "Upstream Records", "Pending Decisions", "Recent Upstream Anomalies", "Recent Decisions", "Feed Status", "Logo Quality"]) {
    if (!document.body.textContent.includes(text)) failures.push(`missing dashboard text: ${text}`);
  }
  if (!document.querySelector('a[href="/channels-epg"]')) failures.push("missing channels management link");
  if (!document.querySelector('a[href="/channel-names"]')) failures.push("missing channel names management link");
  if (!document.querySelector('a[href="/epg-list"]')) failures.push("missing programmes management link");
  if (document.querySelector('script[src*="src/pages/dashboard.ts"]')) failures.push("old demo dashboard entry is still loaded");
  if (!document.querySelector(".om-table-scroll table.om-table, .om-table-shell table.om-table, .table-responsive .table")) failures.push("responsive dashboard table did not render");

  return { failures };
})()
"""


def js_mobile_assertions() -> str:
    """Return in-page JavaScript that checks the mobile shell is not squeezed by the sidebar."""
    return r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const sidebar = document.querySelector(".app-menu, [data-om-sidebar]");
  const content = document.querySelector(".page-content");
  const toggle = document.querySelector("[data-om-sidebar-toggle]");
  const backdrop = document.querySelector("[data-om-sidebar-backdrop]");
  const overflowAllowance = 2;

  if (document.documentElement.scrollWidth > window.innerWidth + overflowAllowance) {
    failures.push(`document has horizontal overflow (${document.documentElement.scrollWidth}px > ${window.innerWidth}px)`);
  }
  if (document.body.scrollWidth > window.innerWidth + overflowAllowance) {
    failures.push(`body has horizontal overflow (${document.body.scrollWidth}px > ${window.innerWidth}px)`);
  }

  if (!sidebar) {
    failures.push("missing mobile sidebar element");
  } else {
    const rect = sidebar.getBoundingClientRect();
    const style = getComputedStyle(sidebar);
    const sidebarOpen = document.documentElement.dataset.omSidebarOpen === "true";
    if (!sidebarOpen && style.opacity !== "0" && rect.right > 1) {
      failures.push(`mobile sidebar is visible by default (right=${rect.right}, opacity=${style.opacity})`);
    }
  }

  if (!toggle || !sidebar) {
    failures.push("missing mobile sidebar toggle");
  } else {
    toggle.click();
    await sleep(250);
    const opened =
      document.documentElement.dataset.omSidebarOpen === "true" &&
      document.body.classList.contains("vertical-sidebar-enable") &&
      sidebar.getBoundingClientRect().right > window.innerWidth * 0.5 &&
      (!backdrop || (!backdrop.hidden && !backdrop.classList.contains("hidden")));
    toggle.click();
    await sleep(250);
    const closed =
      document.documentElement.dataset.omSidebarOpen !== "true" &&
      !document.body.classList.contains("vertical-sidebar-enable") &&
      (!backdrop || (backdrop.hidden && backdrop.classList.contains("hidden")));
    if (!opened || !closed) {
      failures.push(`mobile sidebar toggle did not open then close with backdrop/html/body state (opened=${opened}, closed=${closed})`);
    }
    if (backdrop && document.body.classList.contains("vertical-sidebar-enable")) {
      failures.push("mobile sidebar stayed open after closing from toggle");
    }
  }

  if (!content) {
    failures.push("missing mobile page content element");
  } else {
    const rect = content.getBoundingClientRect();
    if (rect.width < window.innerWidth * 0.8) {
      failures.push(`mobile page content is squeezed (${rect.width}px of ${window.innerWidth}px)`);
    }
  }

  return { failures };
})()
"""


def js_backend_page_assertions(path: str, texts: list[str], selectors: list[str], active_href: str) -> str:
    """返回真实后台 CRUD 页面断言脚本。"""
    payload = json.dumps(
        {
            "path": path,
            "texts": texts,
            "selectors": selectors,
            "activeHref": active_href,
        },
        ensure_ascii=False,
    )
    return (
        r"""
(() => {
  const config =
"""
        + payload
        + r""";
  const failures = [];
  const overflowAllowance = 2;
  if (location.pathname !== config.path) failures.push(`wrong backend path: ${location.pathname}`);
  for (const text of config.texts) {
    if (!document.body.textContent.includes(text)) failures.push(`missing backend text: ${text}`);
  }
  for (const selector of config.selectors) {
    if (!document.querySelector(selector)) failures.push(`missing backend selector: ${selector}`);
  }
  if (config.activeHref) {
    const activeLink = document.querySelector(`a[href="${config.activeHref}"].active`);
    if (!activeLink) failures.push(`sidebar active link missing for ${config.activeHref}`);
  }
  if (document.documentElement.scrollWidth > window.innerWidth + overflowAllowance) {
    failures.push(`document has horizontal overflow on ${config.path}: ${document.documentElement.scrollWidth}px > ${window.innerWidth}px`);
  }
  return { failures };
})()
"""
    )


def js_visual_health_assertions(label: str, *, mobile: bool = False) -> str:
    """返回通用视觉健康断言，覆盖 CSS 丢失、溢出、图片和基础控件状态。"""
    payload = json.dumps({"label": label, "mobile": mobile}, ensure_ascii=False)
    return (
        r"""
(() => {
  const config =
"""
        + payload
        + r""";
  const failures = [];
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const transparent = (value) => !value || value === "transparent" || value === "rgba(0, 0, 0, 0)";
  const overflowAllowance = 2;

  if (document.styleSheets.length === 0) failures.push(`${config.label}: no stylesheets loaded`);
  if (document.documentElement.scrollWidth > window.innerWidth + overflowAllowance) {
    failures.push(`${config.label}: document horizontal overflow ${document.documentElement.scrollWidth}px > ${window.innerWidth}px`);
  }
  if (document.body.scrollWidth > window.innerWidth + overflowAllowance) {
    failures.push(`${config.label}: body horizontal overflow ${document.body.scrollWidth}px > ${window.innerWidth}px`);
  }

  const viewportBoundedSelectors = [
    ".oldman-main",
    ".page-content",
    ".row",
    ".om-card",
    ".card",
    ".om-table-shell",
    ".table-responsive",
    ".om-button",
    ".btn",
    ".dropdown-menu.show",
    "[data-om-component='modal']:not([hidden])",
  ];
  const insideHorizontalScroller = (element) => {
    let current = element.parentElement;
    while (current && current !== document.body) {
      const style = getComputedStyle(current);
      const scrollable = /(auto|scroll)/.test(style.overflowX) && current.scrollWidth > current.clientWidth + overflowAllowance;
      if (scrollable || current.classList.contains("om-table-scroll") || current.classList.contains("table-responsive") || current.classList.contains("table-card")) {
        return true;
      }
      current = current.parentElement;
    }
    return false;
  };
  for (const element of document.querySelectorAll(viewportBoundedSelectors.join(","))) {
    if (!visible(element)) continue;
    if (insideHorizontalScroller(element)) continue;
    const rect = element.getBoundingClientRect();
    if (rect.left < -overflowAllowance || rect.right > window.innerWidth + overflowAllowance) {
      const label = element.id || element.getAttribute("data-om-component") || element.className || element.tagName;
      failures.push(`${config.label}: visible element overflows viewport (${label}: ${Math.round(rect.left)}..${Math.round(rect.right)} of ${window.innerWidth})`);
      break;
    }
  }

  const main = document.querySelector(".oldman-main, .page-content, .auth-page-wrapper");
  if (!visible(main)) failures.push(`${config.label}: main content is not visible`);

  const bodyBg = getComputedStyle(document.body).backgroundColor;
  if (transparent(bodyBg)) failures.push(`${config.label}: body background is transparent`);

  const firstCard = document.querySelector(".om-card, .card");
  if (firstCard) {
    const style = getComputedStyle(firstCard);
    if (transparent(style.backgroundColor)) failures.push(`${config.label}: card background is transparent`);
    if (parseFloat(style.borderRadius) <= 0) failures.push(`${config.label}: card border radius is missing`);
  }

  const firstButton = Array.from(document.querySelectorAll(".om-button, .btn, button, a[role='button']")).find(visible);
  if (firstButton) {
    const style = getComputedStyle(firstButton);
    if (parseFloat(style.paddingLeft) <= 0 || parseFloat(style.paddingRight) <= 0) {
      failures.push(`${config.label}: visible button has no horizontal padding`);
    }
    const rect = firstButton.getBoundingClientRect();
    const minHeight = config.mobile ? 30 : 24;
    if (rect.height < minHeight) failures.push(`${config.label}: visible button height is too small (${rect.height}px)`);
  }

  for (const form of document.querySelectorAll("form")) {
    if (!visible(form)) continue;
    for (const control of form.querySelectorAll("input:not([type='hidden']), select, textarea")) {
      if (!visible(control)) continue;
      const rect = control.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        failures.push(`${config.label}: visible form control has zero size (${control.getAttribute("name") || control.tagName})`);
      }
      if (config.mobile && rect.width > window.innerWidth + overflowAllowance) {
        failures.push(`${config.label}: mobile form control overflows viewport (${control.getAttribute("name") || control.tagName})`);
      }
    }
  }

  for (const image of document.querySelectorAll("img")) {
    if (!visible(image)) continue;
    if (!image.complete) failures.push(`${config.label}: visible image not loaded (${image.getAttribute("src") || ""})`);
    if (image.naturalWidth === 0 && !String(image.getAttribute("src") || "").startsWith("data:")) {
      failures.push(`${config.label}: visible image has zero natural width (${image.getAttribute("src") || ""})`);
    }
  }

  return { failures };
})()
"""
    )


def assert_backend_page(client: CDPClient, base_url: str, path: str, texts: list[str], selectors: list[str], active_href: str, result: VerificationResult) -> None:
    """打开一个真实后台页面并把失败项追加到验证结果。"""
    navigate(client, urllib.parse.urljoin(base_url, path))
    assertion_result = client.evaluate(js_backend_page_assertions(path, texts, selectors, active_href), timeout=10.0)
    failures = assertion_failures(assertion_result)
    result.pageErrors.extend(str(failure) for failure in failures)
    assert_visual_health(client, path, result)


def assert_visual_health(client: CDPClient, label: str, result: VerificationResult, *, mobile: bool = False) -> None:
    """执行通用视觉健康检查。"""
    assertion_result = client.evaluate(js_visual_health_assertions(label, mobile=mobile), timeout=10.0)
    result.pageErrors.extend(str(failure) for failure in assertion_failures(assertion_result))


def assert_programme_datetime_picker(client: CDPClient, label: str, result: VerificationResult) -> None:
    """断言节目时间字段挂载为真正的 datetime picker，而不是裸 date 控件。"""
    assertion_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let input = document.querySelector('input[name="start_date"]');
  const deadline = Date.now() + 3000;
  while (
    input &&
    (input.getAttribute("data-om-component-state") !== "mounted" || !input._flatpickr) &&
    Date.now() < deadline
  ) {
    await sleep(50);
    input = document.querySelector('input[name="start_date"]');
  }
  if (!input) return { failures: ["missing start_date input"] };
  if (input.getAttribute("type") !== "text") failures.push(`start_date type is ${input.getAttribute("type") || ""}`);
  if (input.getAttribute("data-om-component") !== "date-time-picker") failures.push("start_date missing date-time-picker component");
  if (input.getAttribute("data-provider") !== "flatpickr") failures.push("start_date missing flatpickr provider");
  if (!input.hasAttribute("data-enable-time")) failures.push("start_date missing enable-time");
  if (input.getAttribute("data-date-format") !== "Y-m-d\\TH:i") failures.push(`start_date format is ${input.getAttribute("data-date-format") || ""}`);
  if (input.getAttribute("data-om-component-state") !== "mounted") failures.push("start_date picker is not mounted");
  if (!input._flatpickr) failures.push("start_date flatpickr instance missing");
  if (input._flatpickr && input._flatpickr.config.enableTime !== true) failures.push("start_date flatpickr enableTime is not true");
  if (input._flatpickr) {
    input._flatpickr.open();
    await sleep(100);
    const calendar = input._flatpickr.calendarContainer;
    if (!calendar?.classList.contains("open")) failures.push("start_date flatpickr calendar did not open");
    if (!calendar?.querySelector(".flatpickr-time")) failures.push("start_date flatpickr time controls missing");
    input._flatpickr.close();
  }
  return { failures };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_failures(assertion_result))


def assert_programme_channel_remote_fields(client: CDPClient, label: str, result: VerificationResult) -> None:
    """断言节目编辑页远程频道字段已回显，并且 autocomplete 选择会写回隐藏值。"""
    assertion_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const select = document.querySelector("select[name='channel_id']");
  const selected = select?.selectedOptions?.[0];
  const autocompleteRoot = document.querySelector("[data-om-component='autocomplete']");
  const hidden = autocompleteRoot?.querySelector("[data-om-autocomplete-value-control]");
  const input = autocompleteRoot?.querySelector("[data-om-autocomplete-input]");

  if (!select) failures.push("missing channel_id select");
  if (!selected || !selected.value) failures.push("channel_id select has no selected value");
  if (!selected || !(selected.textContent || "").trim()) failures.push("channel_id select has no selected label");
  if (!autocompleteRoot) failures.push("missing channel autocomplete root");
  if (!hidden || !hidden.value) failures.push("channel autocomplete hidden value is empty");
  if (!input || !input.value.trim()) failures.push("channel autocomplete visible value is empty");
  if (selected && hidden && selected.value !== hidden.value) failures.push(`channel select value ${selected.value} differs from autocomplete hidden ${hidden.value}`);

  if (!input || !autocompleteRoot) return { failures };
  input.focus();
  input.value = "a";
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "a" }));
  await sleep(400);
  const item = autocompleteRoot.querySelector("[data-om-autocomplete-item]");
  if (!item) {
    failures.push("channel autocomplete did not render selectable suggestions");
    return { failures };
  }
  const itemValue = item.getAttribute("data-om-autocomplete-item") || "";
  item.click();
  await sleep(50);
  if (!hidden.value) failures.push("channel autocomplete hidden value stayed empty after selecting suggestion");
  if (itemValue && hidden.value !== itemValue) failures.push("channel autocomplete hidden value did not match selected suggestion");
  if (!input.value.trim()) failures.push("channel autocomplete visible value stayed empty after selecting suggestion");
  return { failures };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_failures(assertion_result))


def assert_channel_name_epg_remote_fields(client: CDPClient, label: str, result: VerificationResult) -> None:
    """断言频道名称编辑页远程 EPG 字段已回显，并且 autocomplete 可搜索选择。"""
    assertion_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const select = document.querySelector("select[name='epg_id']");
  const selected = select?.selectedOptions?.[0];
  const autocompleteRoot = document.querySelector("[data-om-component='autocomplete']");
  const hidden = autocompleteRoot?.querySelector("[data-om-autocomplete-value-control]");
  const input = autocompleteRoot?.querySelector("[data-om-autocomplete-input]");

  if (!select) failures.push("missing epg_id select");
  if (!selected || !selected.value) failures.push("epg_id select has no selected value");
  if (!selected || !(selected.textContent || "").trim()) failures.push("epg_id select has no selected label");
  if (!autocompleteRoot) failures.push("missing EPG autocomplete root");
  if (!hidden || !hidden.value) failures.push("EPG autocomplete hidden value is empty");
  if (!input || !input.value.trim()) failures.push("EPG autocomplete visible value is empty");
  if (selected && hidden && selected.value !== hidden.value) failures.push(`epg_id select value ${selected.value} differs from autocomplete hidden ${hidden.value}`);

  if (!input || !autocompleteRoot) return { failures };
  input.focus();
  input.value = "a";
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "a" }));
  await sleep(400);
  const item = autocompleteRoot.querySelector("[data-om-autocomplete-item]");
  if (!item) {
    failures.push("EPG autocomplete did not render selectable suggestions");
    return { failures };
  }
  const itemValue = item.getAttribute("data-om-autocomplete-item") || "";
  item.click();
  await sleep(50);
  if (!hidden.value) failures.push("EPG autocomplete hidden value stayed empty after selecting suggestion");
  if (itemValue && hidden.value !== itemValue) failures.push("EPG autocomplete hidden value did not match selected suggestion");
  if (!input.value.trim()) failures.push("EPG autocomplete visible value stayed empty after selecting suggestion");
  return { failures };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_failures(assertion_result))


def assert_native_datetime_local_control(client: CDPClient, result: VerificationResult) -> None:
    """单独验证浏览器原生 datetime-local 支持日期和时间值。"""
    assertion_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const input = document.createElement("input");
  input.type = "datetime-local";
  input.value = "2026-06-10T12:30";
  document.body.appendChild(input);
  if (input.type !== "datetime-local") failures.push(`native datetime-local type is ${input.type}`);
  if (input.value !== "2026-06-10T12:30") failures.push(`native datetime-local value is ${input.value}`);
  input.stepUp();
  if (!input.value.includes("T")) failures.push(`native datetime-local stepped value lost time separator: ${input.value}`);
  input.remove();
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(f"native datetime-local: {failure}" for failure in assertion_failures(assertion_result))


def install_turbo_probe(client: CDPClient) -> dict[str, Any]:
    """在页面内安装 Turbo 事件探针，用于证明后续点击没有整页刷新。"""
    payload = required_payload_object(
        client.evaluate(
            r"""
(() => {
  const probe = crypto.randomUUID();
  window.__oldmanTurboProbe = probe;
  window.__oldmanTurboCounts = { beforeFetch: 0, beforeRender: 0, render: 0, load: 0, beforeFrameRender: 0, frameRender: 0, frameLoad: 0 };
  document.addEventListener("turbo:before-fetch-request", () => { window.__oldmanTurboCounts.beforeFetch += 1; });
  document.addEventListener("turbo:before-render", () => { window.__oldmanTurboCounts.beforeRender += 1; });
  document.addEventListener("turbo:render", () => { window.__oldmanTurboCounts.render += 1; });
  document.addEventListener("turbo:load", () => { window.__oldmanTurboCounts.load += 1; });
  document.addEventListener("turbo:before-frame-render", () => { window.__oldmanTurboCounts.beforeFrameRender += 1; });
  document.addEventListener("turbo:frame-render", () => { window.__oldmanTurboCounts.frameRender += 1; });
  document.addEventListener("turbo:frame-load", () => { window.__oldmanTurboCounts.frameLoad += 1; });
  const navigationEntries = performance.getEntriesByType("navigation");
  return {
    probe,
    navigationCount: navigationEntries.length,
    navigationType: navigationEntries.at(-1)?.type || "",
    counts: { ...window.__oldmanTurboCounts }
  };
})()
""",
            timeout=5.0,
        ),
        "install Turbo probe",
    )
    required_canonical_uuid(payload, "probe", "install Turbo probe")
    required_non_negative_integer(payload, "navigationCount", "install Turbo probe")
    required_navigation_type(payload, "navigationType", "install Turbo probe")
    required_turbo_counts(payload, "install Turbo probe", initial=True)
    return payload


def wait_for_turbo_path(client: CDPClient, expected_path: str, before: dict[str, Any], label: str, result: VerificationResult, timeout: float = 8.0) -> None:
    """等待 Turbo 导航到目标路径，并验证页面探针没有被整页刷新清空。"""
    before_probe = required_canonical_uuid(before, "probe", f"{label} Turbo before")
    before_navigation_count = required_non_negative_integer(before, "navigationCount", f"{label} Turbo before")
    before_navigation_type = required_navigation_type(before, "navigationType", f"{label} Turbo before")
    before_counts = required_turbo_counts(before, f"{label} Turbo before", initial=True)
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const navigationEntries = performance.getEntriesByType("navigation");
  return {
    path: location.pathname,
    probe: window.__oldmanTurboProbe || "",
    navigationCount: navigationEntries.length,
    navigationType: navigationEntries.at(-1)?.type || "",
    counts: window.__oldmanTurboCounts ? { ...window.__oldmanTurboCounts } : null,
    ready: document.documentElement.dataset.omReady === "true",
    mainFrameState: document.querySelector("#oldman-main")?.dataset.omFrameState || "",
    mainFrameReady: !document.querySelector("#oldman-main") || document.querySelector("#oldman-main")?.dataset.omFrameState === "mounted"
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} Turbo state",
        )
        last_state = state
        path = required_success_string(state, "path", f"{label} Turbo state")
        if not path.startswith("/"):
            raise VerificationError(f"{label} Turbo state path must be absolute")
        probe = required_canonical_uuid(state, "probe", f"{label} Turbo state")
        navigation_count = required_non_negative_integer(state, "navigationCount", f"{label} Turbo state")
        navigation_type = required_navigation_type(state, "navigationType", f"{label} Turbo state")
        counts = required_turbo_counts(state, f"{label} Turbo state")
        ready = required_success_bool(state, "ready", f"{label} Turbo state")
        main_frame_state = required_success_string(state, "mainFrameState", f"{label} Turbo state")
        main_frame_ready = required_success_bool(state, "mainFrameReady", f"{label} Turbo state")
        if any(counts[field_name] < before_counts[field_name] for field_name in TURBO_COUNT_FIELDS):
            raise VerificationError(f"{label} Turbo state counters decreased")
        rendered = counts["render"] > before_counts["render"] or counts["frameRender"] > before_counts["frameRender"]
        if (
            path == expected_path
            and probe == before_probe
            and navigation_count == before_navigation_count
            and navigation_type == before_navigation_type
            and counts["beforeFetch"] > before_counts["beforeFetch"]
            and rendered
            and ready is True
            and main_frame_state == "mounted"
            and main_frame_ready is True
        ):
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: Turbo 导航未通过，最后状态 {last_state}")


def click_and_assert_turbo(client: CDPClient, selector: str, expected_path: str, label: str, result: VerificationResult) -> None:
    """点击真实页面链接，并断言它通过 Turbo 到达目标页面。"""
    before = install_turbo_probe(client)
    click_result = client.evaluate(
        r"""
(async () => {
  const selector =
"""
        + json.dumps(selector)
        + r""";
  const element = document.querySelector(selector);
  if (!element) return { failures: [`missing click target: ${selector}`] };
  element.scrollIntoView({ block: "center", inline: "center" });
  await new Promise((resolve) => requestAnimationFrame(resolve));
  element.click();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    click_result_failures = assertion_failures(click_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in click_result_failures)
    if click_result_failures:
        return
    wait_for_turbo_path(client, expected_path, before, label, result)


def assert_backend_shell_frame_navigation(client: CDPClient, result: VerificationResult) -> None:
    """验证后台导航只替换 oldman-main，sidebar/topbar 外壳节点不被重建。"""
    marker_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const marker = crypto.randomUUID();
  const startPath = location.pathname;
  const sidebar = document.querySelector("#oldman-sidebar-nav");
  const topbar = document.querySelector("#page-topbar");
  const main = document.querySelector("#oldman-main");
  if (!sidebar) failures.push("missing oldman-sidebar-nav frame");
  if (!topbar) failures.push("missing page-topbar");
  if (!main) failures.push("missing oldman-main frame");
  if (sidebar) sidebar.dataset.omShellProbe = `${marker}:sidebar`;
  if (topbar) topbar.dataset.omShellProbe = `${marker}:topbar`;
  if (main) main.dataset.omShellProbe = `${marker}:main`;
  return { failures, marker, startPath };
})()
""",
        timeout=5.0,
    )
    marker_result_failures = assertion_failures(marker_result)
    result.pageErrors.extend(f"backend shell frame navigation: {failure}" for failure in marker_result_failures)
    if marker_result_failures:
        return
    marker = required_canonical_uuid(marker_result, "marker", "backend shell frame navigation")
    start_path = required_success_string(marker_result, "startPath", "backend shell frame navigation")
    if not start_path.startswith("/"):
        raise VerificationError("backend shell frame navigation success payload startPath must begin with /")

    client.evaluate(
        r"""
(() => {
  window.__oldmanMainFramePreloaderProbe = { failures: [], sawLoading: false };
  document.addEventListener("turbo:before-fetch-request", (event) => {
    const target = event.target;
    if (!(target instanceof Element) || !target.closest("#oldman-main")) return;

    // A replacement Page installs its listener after this persistent probe.
    // Observe the completed dispatch, not the incidental listener registration order.
    queueMicrotask(() => {
      const probe = window.__oldmanMainFramePreloaderProbe;
      const mainFrame = document.querySelector("#oldman-main");
      const fullscreen = document.querySelector("#preloader");
      const scoped = mainFrame?.querySelector(":scope > [data-om-scoped-preloader]");
      probe.sawLoading = true;

      if (fullscreen && !fullscreen.hidden) {
        probe.failures.push("fullscreen preloader displayed during main frame navigation");
      }
      if (!scoped) {
        probe.failures.push("oldman-main scoped preloader is missing");
      }
      if (mainFrame?.dataset.omPreloaderStatus !== "loading") {
        probe.failures.push(`oldman-main scoped preloader status is ${mainFrame?.dataset.omPreloaderStatus || "(empty)"}`);
      }
    });
  });
})()
""",
        timeout=5.0,
    )

    def assert_shell_unchanged(label: str, expected_active_href: str) -> None:
        """确认内容区导航后后台外壳节点仍复用，且侧边栏高亮同步到新地址。"""
        assertion_result = client.evaluate(
            r"""
(() => {
  const marker =
"""
            + json.dumps(marker)
            + r""";
  const label =
"""
            + json.dumps(label)
            + r""";
  const failures = [];
  if (document.querySelector("#oldman-sidebar-nav")?.dataset.omShellProbe !== `${marker}:sidebar`) {
    failures.push("shell sidebar node changed");
  }
  if (document.querySelector("#page-topbar")?.dataset.omShellProbe !== `${marker}:topbar`) {
    failures.push("shell topbar node changed");
  }
  if (document.querySelector("#oldman-main")?.dataset.omShellProbe !== `${marker}:main`) {
    failures.push(`${label} content link changed shell node`);
  }
  const active = document.querySelector("#navbar-nav a.nav-link.active[href]:not([href^='#'])");
  const activeHref = active?.getAttribute("href") || "";
  const expectedActiveHref =
"""
            + json.dumps(expected_active_href)
            + r""";
  if (activeHref !== expectedActiveHref) {
    failures.push(`${label} sidebar active href is ${activeHref || "(empty)"}, expected ${expectedActiveHref}`);
  }
  const mainFrame = document.querySelector("#oldman-main");
  const preloaderProbe = window.__oldmanMainFramePreloaderProbe || { failures: [], sawLoading: false };
  failures.push(...preloaderProbe.failures);
  if (!preloaderProbe.sawLoading) {
    failures.push(`${label} oldman-main scoped preloader did not observe loading`);
  }
  if (mainFrame?.dataset.omPreloaderStatus !== "idle") {
    failures.push(`${label} oldman-main scoped preloader status is ${mainFrame?.dataset.omPreloaderStatus || "(empty)"}`);
  }
  if (mainFrame?.querySelector(":scope > [data-om-scoped-preloader]")) {
    const overlays = Array.from(mainFrame.querySelectorAll(":scope > [data-om-scoped-preloader]")).map((overlay) => {
      const scope = overlay.closest("[data-om-preloader-status]");
      const component = overlay.closest("[data-om-component]");
      const container = overlay.parentElement;
      return {
        text: overlay.textContent.trim(),
        parent: container ? `${container.tagName.toLowerCase()}#${container.id || ""}.${Array.from(container.classList).join(".")}` : "",
        scope: scope ? `${scope.tagName.toLowerCase()}#${scope.id || ""}.${Array.from(scope.classList).join(".")}[${scope.dataset.omPreloaderStatus || ""}]` : "",
        component: component ? `${component.tagName.toLowerCase()}#${component.id || ""}[${component.getAttribute("data-om-component") || ""}:${component.getAttribute("data-om-component-state") || ""}]` : "",
        frameState: mainFrame.dataset.omFrameState || "",
        html: overlay.outerHTML.slice(0, 240)
      };
    });
    failures.push(`${label} oldman-main scoped preloader remains after navigation: ${JSON.stringify(overlays)}`);
  }
  window.__oldmanMainFramePreloaderProbe = { failures: [], sawLoading: false };
  return { failures };
})()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_failures(assertion_result))

    def assert_browser_back_idle_after_frame_nav(expected_path: str, label: str) -> None:
        """确认 frame 导航后浏览器后退不会留下内容区 loading 状态。"""
        before = install_turbo_probe(client)
        before_probe = required_canonical_uuid(before, "probe", f"{label} browser-back before")
        before_navigation_count = required_non_negative_integer(before, "navigationCount", f"{label} browser-back before")
        client.evaluate(
            r"""
(() => {
  window.__oldmanMainFrameBackProbe = { failures: [], sawLoading: false };
  document.addEventListener("turbo:before-fetch-request", (event) => {
    const target = event.target;
    if (!(target instanceof Element) || !target.closest("#oldman-main")) return;
    setTimeout(() => {
      const mainFrame = document.querySelector("#oldman-main");
      if (
        mainFrame?.dataset.omPreloaderStatus === "loading" ||
        mainFrame?.querySelector(":scope > [data-om-scoped-preloader]") ||
        mainFrame?.dataset.omFrameState === "loading"
      ) {
        window.__oldmanMainFrameBackProbe.sawLoading = true;
      }
    }, 0);
  }, { once: true });
  history.back();
})()
""",
            timeout=5.0,
        )
        deadline = time.monotonic() + 8.0
        last_state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            state = required_browser_back_state(
                client.evaluate(
                    r"""
(() => {
  const navigationEntries = performance.getEntriesByType("navigation");
  const mainFrame = document.querySelector("#oldman-main");
  const probe = window.__oldmanMainFrameBackProbe || { failures: [], sawLoading: false };
  return {
    path: location.pathname,
    probe: window.__oldmanTurboProbe || "",
    navigationCount: navigationEntries.length,
    mainFrameState: mainFrame?.dataset.omFrameState || "",
    mainFramePreloaderStatus: mainFrame?.dataset.omPreloaderStatus || "",
    hasScopedPreloader: Boolean(mainFrame?.querySelector(":scope > [data-om-scoped-preloader]")),
    sawLoading: Boolean(probe.sawLoading),
    ready: document.documentElement.dataset.omReady === "true"
  };
})()
""",
                    timeout=5.0,
                ),
                f"{label} browser-back state",
            )
            last_state = state
            if (
                state["path"] == expected_path
                and state["probe"] == before_probe
                and state["navigationCount"] == before_navigation_count
                and state["ready"] is True
                and state["mainFrameState"] == "mounted"
                and state["mainFramePreloaderStatus"] == "idle"
                and state["hasScopedPreloader"] is False
                and state["sawLoading"] is False
            ):
                settled = required_browser_back_state(
                    client.evaluate(
                        r"""
(async () => {
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  await new Promise((resolve) => setTimeout(resolve, 250));
  const mainFrame = document.querySelector("#oldman-main");
  const navigationEntries = performance.getEntriesByType("navigation");
  const probe = window.__oldmanMainFrameBackProbe || { sawLoading: false };
  return {
    path: location.pathname,
    probe: window.__oldmanTurboProbe || "",
    navigationCount: navigationEntries.length,
    mainFrameState: mainFrame?.dataset.omFrameState || "",
    mainFramePreloaderStatus: mainFrame?.dataset.omPreloaderStatus || "",
    hasScopedPreloader: Boolean(mainFrame?.querySelector(":scope > [data-om-scoped-preloader]")),
    sawLoading: Boolean(probe.sawLoading),
    ready: document.documentElement.dataset.omReady === "true"
  };
})()
""",
                        timeout=5.0,
                    ),
                    f"{label} browser-back settled state",
                )
                last_state = settled
                if (
                    settled["path"] == expected_path
                    and settled["probe"] == before_probe
                    and settled["navigationCount"] == before_navigation_count
                    and settled["ready"] is True
                    and settled["mainFrameState"] == "mounted"
                    and settled["mainFramePreloaderStatus"] == "idle"
                    and settled["hasScopedPreloader"] is False
                    and settled["sawLoading"] is False
                ):
                    return
            time.sleep(0.1)

        result.pageErrors.append(f"{label} left oldman-main loading: {last_state}")

    click_and_assert_turbo(client, 'a[href="/channels-epg"]', "/channels-epg", "backend shell sidebar frame nav", result)
    assert_shell_unchanged("sidebar frame nav", "/channels-epg")
    wait_for_table_refresh_complete(client, "/channels-epg/table", "backend shell sidebar table", result)
    click_and_assert_turbo(client, '#oldman-main a[href="/channels-epg/new"]', "/channels-epg/new", "backend shell content frame nav", result)
    assert_shell_unchanged("content frame nav", "/channels-epg")
    assert_browser_back_idle_after_frame_nav("/channels-epg", "browser back after content frame nav")
    wait_for_table_refresh_complete(client, "/channels-epg/table", "browser back restored sidebar table", result)
    assert_browser_back_idle_after_frame_nav(start_path, "browser back after sidebar frame nav")


def submit_filter_and_assert_turbo(client: CDPClient, page_path: str, input_selector: str, value: str, label: str, result: VerificationResult) -> None:
    """提交真实列表筛选表单，并断言筛选结果通过 Turbo 返回。"""
    before = install_turbo_probe(client)
    submit_result = client.evaluate(
        r"""
(async () => {
  const inputSelector =
"""
        + json.dumps(input_selector)
        + r""";
  const value =
"""
        + json.dumps(value)
        + r""";
  const input = document.querySelector(inputSelector);
  const form = input?.closest("form");
  const failures = [];
  if (!input) failures.push(`missing filter input: ${inputSelector}`);
  if (!form) failures.push("missing filter form");
  if (form?.dataset.turbo === "false") failures.push("filter form disables Turbo");
  if (failures.length) return { failures };
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    submit_result_failures = assertion_failures(submit_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in submit_result_failures)
    if submit_result_failures:
        return
    wait_for_turbo_path(client, page_path, before, label, result)


def assert_business_shell_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证顶栏和侧栏的真实交互，避免只截图不点击导致漏检。"""
    assertion_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };

  const languageToggle =
    document.querySelector("[data-om-component='language-switcher'] [data-om-dropdown-toggle]") ||
    document.querySelector("#header-lang-img")?.closest("[data-bs-toggle='dropdown']");
  if (!languageToggle) {
    failures.push("missing language dropdown toggle");
  } else {
    languageToggle.click();
    await sleep(100);
    const languageMenu = languageToggle.closest("[data-om-component='dropdown'], .oldman-dropdown, .dropdown")?.querySelector("[data-om-dropdown-menu], .dropdown-menu");
    if (!visible(languageMenu)) failures.push("language dropdown did not open");
    document.body.click();
    await sleep(50);
  }

  const darkButton = document.querySelector(".light-dark-mode");
  if (!darkButton) {
    failures.push("missing dark mode button");
  } else {
    const before = document.documentElement.getAttribute("data-theme") || "light";
    darkButton.click();
    await sleep(100);
    const after = document.documentElement.getAttribute("data-theme") || "light";
    if (before === after) failures.push("dark mode button did not switch data-theme");
    darkButton.click();
    await sleep(50);
  }

  const fullscreenButton = document.querySelector('[data-toggle="fullscreen"]');
  if (!fullscreenButton) {
    failures.push("missing fullscreen button");
  } else {
    fullscreenButton.click();
    await sleep(100);
    if (!document.body.classList.contains("fullscreen-enable") && !document.fullscreenElement) {
      failures.push("fullscreen button did not change body or fullscreen state");
    }
    if (document.fullscreenElement) await document.exitFullscreen();
    document.body.classList.remove("fullscreen-enable");
  }

  const sidebarButton = document.querySelector("#topnav-hamburger-icon, [data-om-sidebar-toggle]");
  if (!sidebarButton) {
    failures.push("missing sidebar toggle button");
  } else {
    const before = document.documentElement.getAttribute("data-sidebar-size") || "";
    sidebarButton.click();
    await sleep(100);
    const after = document.documentElement.getAttribute("data-sidebar-size") || "";
    if (before === after && !document.body.classList.contains("vertical-sidebar-enable")) {
      failures.push("sidebar toggle did not change sidebar state");
    }
  }

  return { failures };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(str(failure) for failure in assertion_failures(assertion_result))


def wait_for_table_ready(client: CDPClient, table_endpoint: str, label: str, result: VerificationResult, timeout: float = 8.0) -> None:
    """等待服务端表格首个远程响应完成并替换初始占位。"""
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const endpoint =
"""
            + json.dumps(table_endpoint)
            + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const filterForm = Array.from(document.querySelectorAll("form[data-om-component='table-filter-form'][data-om-table-target]"))
    .find((form) => {
      const target = form.getAttribute("data-om-table-target");
      if (!target) return false;
      try {
        return document.querySelector(target) === root;
      } catch {
        return false;
      }
    });
  const hasFilterControl = Boolean(root?.querySelector("[data-om-table-filter]") || filterForm?.querySelector("[name='q']"));
  return {
    hasRoot: Boolean(root),
    hasTable: Boolean(root?.querySelector(".om-table-shell table, .table-responsive table")),
    hasSummary: Boolean(root?.querySelector("[data-om-table-summary]")),
    summaryText: root?.querySelector("[data-om-table-summary]")?.textContent || "",
    hasFilter: Boolean(root?.querySelector("[data-om-table-filter]")),
    hasFilterForm: Boolean(filterForm),
    hasFilterControl,
    hasSort: Boolean(root?.querySelector("[data-om-table-sort]")),
    hasInitialPartial: Boolean(root?.querySelector("[data-om-initial-table-partial]")),
    status: root?.dataset.omStatus || "",
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} table ready state",
        )
        last_state = state
        has_root = required_success_bool(state, "hasRoot", f"{label} table ready state")
        has_table = required_success_bool(state, "hasTable", f"{label} table ready state")
        has_summary = required_success_bool(state, "hasSummary", f"{label} table ready state")
        required_string_field(state, "summaryText", f"{label} table ready state")
        required_success_bool(state, "hasFilter", f"{label} table ready state")
        required_success_bool(state, "hasFilterForm", f"{label} table ready state")
        has_filter_control = required_success_bool(state, "hasFilterControl", f"{label} table ready state")
        has_sort = required_success_bool(state, "hasSort", f"{label} table ready state")
        has_initial_partial = required_success_bool(state, "hasInitialPartial", f"{label} table ready state")
        status = required_string_field(state, "status", f"{label} table ready state")
        if has_root and has_table and has_summary and has_filter_control and has_sort and not has_initial_partial and status == "success":
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: 表格首屏局部加载未通过，最后状态 {last_state}")


def wait_for_table_success(client: CDPClient, table_endpoint: str, label: str, result: VerificationResult, timeout: float = 8.0) -> None:
    """等待服务端表格完成最近一次刷新，避免后续交互点到即将被替换的旧 DOM。"""
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const endpoint =
"""
            + json.dumps(table_endpoint)
            + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  return {
    hasRoot: Boolean(root),
    status: root?.dataset.omStatus || "",
    hasRow: Boolean(root?.querySelector("[data-om-table-row]"))
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} table success state",
        )
        last_state = state
        has_root = required_success_bool(state, "hasRoot", f"{label} table success state")
        status = required_string_field(state, "status", f"{label} table success state")
        has_row = required_success_bool(state, "hasRow", f"{label} table success state")
        if has_root and status == "success" and has_row:
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: 表格刷新完成等待未通过，最后状态 {last_state}")


def wait_for_table_refresh_complete(client: CDPClient, table_endpoint: str, label: str, result: VerificationResult, timeout: float = 8.0) -> None:
    """等待服务端表格刷新结束；筛选场景允许结果为空。"""
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const endpoint =
"""
            + json.dumps(table_endpoint)
            + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  return {
    hasRoot: Boolean(root),
    status: root?.dataset.omStatus || ""
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} table refresh state",
        )
        last_state = state
        has_root = required_success_bool(state, "hasRoot", f"{label} table refresh state")
        status = required_string_field(state, "status", f"{label} table refresh state")
        if has_root and status == "success":
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: 表格刷新结束等待未通过，最后状态 {last_state}")


def install_request_probe(client: CDPClient) -> dict[str, Any]:
    """安装 XHR/fetch 探针，用于验证表格交互没有整页刷新。"""
    payload = required_payload_object(
        client.evaluate(
            r"""
(() => {
  window.__oldmanRequestLog = [];
  if (!window.__oldmanRequestProbeInstalled) {
    window.__oldmanRequestProbeInstalled = true;
    const originalFetch = window.fetch;
    if (originalFetch) {
      window.fetch = function(input, init) {
        const url = typeof input === "string" ? input : input?.url || "";
        const headers = {};
        const sourceHeaders = new Headers(init?.headers || {});
        sourceHeaders.forEach((value, key) => { headers[key.toLowerCase()] = value; });
        window.__oldmanRequestLog.push({ type: "fetch", url: String(url), method: init?.method || "GET", headers });
        return originalFetch.apply(this, arguments);
      };
    }
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
      this.__oldmanRequest = { type: "xhr", method: String(method || "GET"), url: String(url || ""), headers: {} };
      return originalOpen.apply(this, arguments);
    };
    const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
      if (this.__oldmanRequest) this.__oldmanRequest.headers[String(name).toLowerCase()] = String(value);
      return originalSetRequestHeader.apply(this, arguments);
    };
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
      if (this.__oldmanRequest) window.__oldmanRequestLog.push(this.__oldmanRequest);
      return originalSend.apply(this, arguments);
    };
  }
  const navigationEntries = performance.getEntriesByType("navigation");
  window.__oldmanPageProbe = crypto.randomUUID();
  return {
    path: location.pathname,
    origin: location.origin,
    probe: window.__oldmanPageProbe,
    navigationCount: navigationEntries.length,
  };
})()
""",
            timeout=5.0,
        ),
        "install request probe",
    )
    path = required_success_string(payload, "path", "install request probe")
    if not path.startswith("/"):
        raise VerificationError("install request probe payload path must be absolute")
    required_success_string(payload, "origin", "install request probe")
    required_canonical_uuid(payload, "probe", "install request probe")
    required_non_negative_integer(payload, "navigationCount", "install request probe")
    return payload


def wait_for_table_request(client: CDPClient, table_endpoint: str, required_params: dict[str, str], before: dict[str, Any], label: str, result: VerificationResult, timeout: float = 8.0) -> None:
    """等待表格交互请求出现，并验证页面没有发生整页刷新。"""
    wait_for_endpoint_request(client, table_endpoint, required_params, before, label, result, timeout=timeout, noun="表格局部请求")


def wait_for_endpoint_request(
    client: CDPClient,
    endpoint: str,
    required_params: dict[str, str],
    before: dict[str, Any],
    label: str,
    result: VerificationResult,
    timeout: float = 8.0,
    noun: str = "局部请求",
) -> None:
    """等待指定 endpoint 请求出现，并验证页面没有发生整页刷新。"""
    before_path = required_success_string(before, "path", f"{label} request before")
    if not before_path.startswith("/"):
        raise VerificationError(f"{label} request before path must be absolute")
    before_origin = required_success_string(before, "origin", f"{label} request before")
    before_probe = required_canonical_uuid(before, "probe", f"{label} request before")
    before_navigation_count = required_non_negative_integer(before, "navigationCount", f"{label} request before")
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const endpoint =
"""
            + json.dumps(endpoint)
            + r""";
  const requiredParams =
"""
            + json.dumps(required_params)
            + r""";
  const navigationEntries = performance.getEntriesByType("navigation");
  const requests = window.__oldmanRequestLog || [];
  const matched = requests.find((request) => {
    const url = new URL(request.url, location.href);
    if (url.pathname !== endpoint) return false;
    return Object.entries(requiredParams).every(([key, value]) => url.searchParams.get(key) === value);
  });
  return {
    path: location.pathname,
    origin: location.origin,
    probe: window.__oldmanPageProbe || "",
    navigationCount: navigationEntries.length,
    requests,
    matched: Boolean(matched),
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} request state",
        )
        last_state = state
        path = required_success_string(state, "path", f"{label} request state")
        if not path.startswith("/"):
            raise VerificationError(f"{label} request state path must be absolute")
        origin = required_success_string(state, "origin", f"{label} request state")
        probe = required_canonical_uuid(state, "probe", f"{label} request state")
        navigation_count = required_non_negative_integer(state, "navigationCount", f"{label} request state")
        requests = required_request_records(state, f"{label} request state")
        matched = required_success_bool(state, "matched", f"{label} request state")
        recomputed_match = False
        for request in requests:
            parsed = urllib.parse.urlparse(urllib.parse.urljoin(f"{origin}/", request["url"]))
            request_origin = f"{parsed.scheme}://{parsed.netloc}"
            if request_origin != origin or parsed.path != endpoint:
                continue
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if all(query.get(key, [None])[0] == value for key, value in required_params.items()):
                recomputed_match = True
                break
        if matched is not recomputed_match:
            raise VerificationError(f"{label} request state matched disagrees with requests")
        if (
            matched
            and path == before_path
            and origin == before_origin
            and probe == before_probe
            and navigation_count == before_navigation_count
        ):
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: {noun}未通过，最后状态 {last_state}")


def js_table_sort_state_assertion(table_endpoint: str) -> str:
    """返回远程刷新后表头排序状态仍保留在原表头节点上的断言脚本。"""
    return (
        r"""
(() => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
  const failures = [];
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const sort = root?.querySelector("[data-om-table-sort][aria-sort='ascending'], [data-om-table-sort][aria-sort='descending']");
  const header = sort?.closest("th");
  if (!sort) failures.push("刷新后找不到当前排序按钮");
  if (!header) failures.push("刷新后排序按钮不在 th 内");
  if (root?.querySelector("thead") !== window.__oldmanSortHeadProbe) failures.push("刷新完成后表头节点被替换");
  if (sort?.getAttribute("aria-sort") === "ascending" && !header?.classList.contains("sorting_asc")) failures.push("刷新后升序表头缺少 sorting_asc");
  if (sort?.getAttribute("aria-sort") === "descending" && !header?.classList.contains("sorting_desc")) failures.push("刷新后降序表头缺少 sorting_desc");
  return { failures };
})()
"""
    )


def js_table_filter_form_helpers() -> str:
    """返回列表门禁复用的外部筛选表单查找和清理脚本。"""
    return r"""
  const tableFilterFormForRoot = (root) => {
    if (!root) return null;
    return Array.from(document.querySelectorAll("form[data-om-component='table-filter-form'][data-om-table-target]"))
      .find((form) => {
        const target = form.getAttribute("data-om-table-target");
        if (!target) return false;
        try {
          return document.querySelector(target) === root;
        } catch {
          return false;
        }
      }) || null;
  };
  const clearTableFilterForm = (form) => {
    if (!form) return;
    for (const field of form.querySelectorAll("input[name], select[name], textarea[name]")) {
      if (field instanceof HTMLInputElement && field.type === "hidden" && field.name === "csrfmiddlewaretoken") continue;
      if (field instanceof HTMLInputElement && (field.type === "checkbox" || field.type === "radio")) {
        field.checked = false;
      } else if (field instanceof HTMLSelectElement && field.multiple) {
        for (const option of field.options) option.selected = false;
      } else {
        field.value = "";
      }
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };
"""


def clear_request_probe(client: CDPClient) -> None:
    """清空页面内请求探针日志，避免前一个交互影响后一个断言。"""
    client.evaluate("window.__oldmanRequestLog = []", timeout=5.0)


def clear_transient_browser_overlays(client: CDPClient) -> None:
    """清理上一段交互残留的 SweetAlert 和 modal 浮层，避免污染下一段门禁。"""
    client.evaluate(
        r"""
(async () => {
  document.querySelectorAll(".swal2-container, .modal-backdrop, .om-modal-backdrop, [data-om-modal-backdrop]").forEach((element) => element.remove());
  document.body.classList.remove("modal-open", "om-modal-open", "swal2-shown", "swal2-height-auto", "swal2-toast-shown");
  document.body.style.removeProperty("overflow");
  document.body.style.removeProperty("padding-right");
  await new Promise((resolve) => requestAnimationFrame(() => resolve(true)));
  return true;
})()
""",
        timeout=5.0,
    )


def assert_list_interactions(client: CDPClient, path: str, active_href: str, table_endpoint: str, result: VerificationResult, *, require_pagination: bool = True) -> None:
    """验证列表页搜索、排序、分页都通过 Table data endpoint 局部刷新。"""
    active_result = client.evaluate(js_backend_page_assertions(path, [], [], active_href), timeout=10.0)
    active_result_failures = assertion_failures(active_result)
    result.pageErrors.extend(str(failure) for failure in active_result_failures)
    wait_for_table_ready(client, table_endpoint, path, result)
    if any(error.startswith(f"{path}: 表格首屏") for error in result.pageErrors):
        return

    capability_result = client.evaluate(
        r"""
(() => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
  const failures = [];
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  if (!root) return { failures: ["missing table root"] };
  const pageSizeControl = root.querySelector("[data-om-table-page-size-control]");
  const tableShell = root.querySelector(".om-table-shell");
  const summary = root.querySelector("[data-om-table-summary]");
  if (!pageSizeControl) {
    failures.push("missing page size control");
  } else if (visible(tableShell) && visible(summary)) {
    const controlRect = pageSizeControl.getBoundingClientRect();
    const shellRect = tableShell.getBoundingClientRect();
    const summaryRect = summary.getBoundingClientRect();
    if (controlRect.width < 56) failures.push("table page-size control is too narrow to show its value");
    if (pageSizeControl.options.length === 0 || !pageSizeControl.selectedOptions[0]?.textContent?.trim()) {
      failures.push("table page-size control has no visible selected value");
    }
    if (controlRect.top < shellRect.bottom - 2) failures.push("table page-size control is rendered above the table instead of in the footer");
    if (controlRect.left > summaryRect.left + 2) failures.push("table page-size control appears after the summary instead of before it");
  }
  if (!root.querySelector("[data-om-table-select-all]")) failures.push("missing select all checkbox");
  if (!root.querySelector("[data-om-table-select-row]")) failures.push("missing row checkbox");
  if (!root.querySelector(".om-badge, .badge")) failures.push("missing badge cell rendering");
  const actionToggle = root.querySelector("[data-om-dropdown-toggle], .dropdown [data-bs-toggle='dropdown']");
  if (!actionToggle) {
    failures.push("missing action dropdown toggle");
  } else {
    actionToggle.click();
    const menu = actionToggle.parentElement?.querySelector("[data-om-dropdown-menu], .dropdown-menu") || actionToggle.closest("td")?.querySelector("[data-om-dropdown-menu], .dropdown-menu");
    if (!menu || menu.hidden || (!menu.classList.contains("show") && menu.classList.contains("hidden"))) failures.push("action dropdown did not open");
    document.body.click();
  }
  const sort = root.querySelector("[data-om-table-sort]");
  const sortHeader = sort?.closest("th");
  const sortIcon = sort?.querySelector("[data-om-table-sort-icon], i");
  if (!sortHeader) failures.push("missing sortable header");
  if (sort && !sort.classList.contains("om-sort-header")) failures.push("sortable header button is missing om-sort-header class");
  if (sort && !sortIcon) failures.push("missing sortable header icon");
  if (sortIcon) {
    const pseudo = getComputedStyle(sortIcon, "::before");
    const maskImage = pseudo.maskImage || pseudo.webkitMaskImage || "";
    if (!maskImage.includes("url(") || ["none", "normal", ""].includes(pseudo.content)) {
      failures.push("sortable header icon has no rendered mask");
    }
  }
  if (sort && sort.querySelectorAll("[data-om-table-sort-icon], i").length > 1) failures.push("sortable header still contains duplicate inline sort icon");
  return { failures };
})()
""",
        timeout=5.0,
    )
    capability_result_failures = assertion_failures(capability_result)
    result.pageErrors.extend(f"{path} capabilities: {failure}" for failure in capability_result_failures)

    before = install_request_probe(client)
    clear_request_probe(client)
    search_result = client.evaluate(
        r"""
(async () => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
"""
        + js_table_filter_form_helpers()
        + r"""
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const filterForm = tableFilterFormForRoot(root);
  if (filterForm) {
    const q = filterForm.querySelector("[name='q']");
    if (!q) return { failures: ["missing table filter form q input"] };
    q.value = "Browser Gate";
    q.dispatchEvent(new Event("input", { bubbles: true }));
    filterForm.requestSubmit();
    return { failures: [] };
  }

  const input = root?.querySelector("[data-om-table-filter]");
  if (!input) return { failures: ["missing table search input"] };
  input.value = "Browser Gate";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    search_result_failures = assertion_failures(search_result)
    result.pageErrors.extend(f"{path} search: {failure}" for failure in search_result_failures)
    if not search_result_failures:
        wait_for_table_request(client, table_endpoint, {"q": "Browser Gate"}, before, f"{path} search", result)

    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
"""
        + js_table_filter_form_helpers()
        + r"""
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const filterForm = tableFilterFormForRoot(root);
  const channel = filterForm?.querySelector("select[name='channel_id']");
  if (!channel) return { failures: [], skipped: true };
  const option = Array.from(channel.options).find((item) => item.value);
  if (!option) return { failures: ["missing non-empty channel filter option"], skipped: false };
  const q = filterForm.querySelector("[name='q']");
  if (q) q.value = "";
  channel.value = option.value;
  channel.dispatchEvent(new Event("change", { bubbles: true }));
  filterForm.requestSubmit();
  return { failures: [], skipped: false, value: option.value };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"{path} channel filter: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        skipped = required_success_bool(filter_result, "skipped", f"{path} channel filter")
        if skipped and path == "/epg-list":
            raise VerificationError("/epg-list channel filter success payload cannot be skipped")
        if skipped:
            value = None
        else:
            value = required_success_string(filter_result, "value", f"{path} channel filter")
    else:
        value = None
    if value is not None:
        wait_for_table_request(
            client,
            table_endpoint,
            {"filter.channel_id": value},
            before,
            f"{path} channel filter",
            result,
        )

    clear_request_probe(client)
    sort_result = client.evaluate(
        r"""
(async () => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const input = root?.querySelector("[data-om-table-filter]");
  const sort = root?.querySelector("[data-om-table-sort]");
  if (input) input.value = "";
  if (!sort) return { failures: ["missing table sort button"] };
  const headBefore = root.querySelector("thead");
  window.__oldmanSortHeadProbe = headBefore;
  sort.click();
  const header = sort.closest("th");
  return {
    failures: [],
    sort: sort.getAttribute("data-om-table-sort") || "",
    ariaSort: sort.getAttribute("aria-sort") || "",
    headerClass: header?.className || "",
    sameHead: root.querySelector("thead") === headBefore,
  };
})()
""",
        timeout=5.0,
    )
    sort_result_failures = assertion_failures(sort_result)
    result.pageErrors.extend(f"{path} sort: {failure}" for failure in sort_result_failures)
    if not sort_result_failures:
        sort_value = required_success_string(sort_result, "sort", f"{path} sort")
        if sort_value.startswith("-"):
            raise VerificationError(f"{path} sort success payload sort must be ascending")
        if required_success_string(sort_result, "ariaSort", f"{path} sort") != "ascending":
            raise VerificationError(f"{path} sort success payload ariaSort must be ascending")
        header_class = required_success_string(sort_result, "headerClass", f"{path} sort")
        if "sorting_asc" not in header_class.split():
            raise VerificationError(f"{path} sort success payload headerClass must contain sorting_asc")
        if required_success_bool(sort_result, "sameHead", f"{path} sort") is not True:
            raise VerificationError(f"{path} sort: 排序刷新替换了表头")
        wait_for_table_request(client, table_endpoint, {"sort": sort_value}, before, f"{path} sort", result)
        sort_after_result = client.evaluate(js_table_sort_state_assertion(table_endpoint), timeout=5.0)
        sort_after_result_failures = assertion_failures(sort_after_result)
        result.pageErrors.extend(f"{path} sort after refresh: {failure}" for failure in sort_after_result_failures)

    clear_request_probe(client)
    sort_desc_result = client.evaluate(
        r"""
(() => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const sort = root?.querySelector("[data-om-table-sort]");
  const headBefore = root?.querySelector("thead");
  if (!sort) return { failures: ["missing table sort button for second click"] };
  window.__oldmanSortHeadProbe = headBefore;
  sort.click();
  const header = sort.closest("th");
  return {
    failures: [],
    sort: `-${sort.getAttribute("data-om-table-sort") || ""}`,
    ariaSort: sort.getAttribute("aria-sort") || "",
    headerClass: header?.className || "",
    sameHead: root?.querySelector("thead") === headBefore,
  };
})()
""",
        timeout=5.0,
    )
    sort_desc_result_failures = assertion_failures(sort_desc_result)
    result.pageErrors.extend(f"{path} sort desc: {failure}" for failure in sort_desc_result_failures)
    if not sort_desc_result_failures:
        sort_desc_value = required_success_string(sort_desc_result, "sort", f"{path} sort desc")
        if not sort_desc_value.startswith("-") or len(sort_desc_value) == 1:
            raise VerificationError(f"{path} sort desc success payload sort must begin with -")
        if required_success_string(sort_desc_result, "ariaSort", f"{path} sort desc") != "descending":
            raise VerificationError(f"{path} sort desc success payload ariaSort must be descending")
        header_class = required_success_string(sort_desc_result, "headerClass", f"{path} sort desc")
        if "sorting_desc" not in header_class.split():
            raise VerificationError(f"{path} sort desc success payload headerClass must contain sorting_desc")
        if required_success_bool(sort_desc_result, "sameHead", f"{path} sort desc") is not True:
            raise VerificationError(f"{path} sort desc: 排序刷新替换了表头")
        wait_for_table_request(client, table_endpoint, {"sort": sort_desc_value}, before, f"{path} sort desc", result)
        sort_desc_after_result = client.evaluate(js_table_sort_state_assertion(table_endpoint), timeout=5.0)
        sort_desc_after_result_failures = assertion_failures(sort_desc_after_result)
        result.pageErrors.extend(f"{path} sort desc after refresh: {failure}" for failure in sort_desc_after_result_failures)

    clear_request_probe(client)
    page_size_result = client.evaluate(
        r"""
(() => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  const control = root?.querySelector("[data-om-table-page-size-control]");
  if (!control) return { failures: ["missing table page size control"] };
  const options = Array.from(control.options || []).map((option) => option.value).filter(Boolean);
  const nextValue = options.find((value) => value !== control.value) || control.value;
  control.value = nextValue;
  control.dispatchEvent(new Event("change", { bubbles: true }));
  return { failures: [], pageSize: nextValue };
})()
""",
        timeout=5.0,
    )
    page_size_result_failures = assertion_failures(page_size_result)
    result.pageErrors.extend(f"{path} page size: {failure}" for failure in page_size_result_failures)
    if not page_size_result_failures:
        page_size = required_positive_integer_string(page_size_result, "pageSize", f"{path} page size")
        wait_for_table_request(client, table_endpoint, {"page_size": page_size, "page": "1"}, before, f"{path} page size", result)
        wait_for_table_success(client, table_endpoint, f"{path} page size", result)

    if not require_pagination:
        return

    clear_request_probe(client)
    pagination_result = client.evaluate(
        r"""
(async () => {
  const endpoint =
"""
        + json.dumps(table_endpoint)
        + r""";
"""
        + js_table_filter_form_helpers()
        + r"""
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === endpoint);
  if (!root) return { failures: ["missing table root"] };
  const filterForm = tableFilterFormForRoot(root);
  if (filterForm) {
    clearTableFilterForm(filterForm);
    filterForm.requestSubmit();
  }
  const input = root.querySelector("[data-om-table-filter]");
  if (input) {
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  const control = root.querySelector("[data-om-table-page-size-control]");
  const firstPageSize = control?.options?.item(0)?.value || root.getAttribute("data-om-table-page-size") || "";
  if (control && firstPageSize && control.value !== firstPageSize) {
    control.value = firstPageSize;
    control.dispatchEvent(new Event("change", { bubbles: true }));
  }
  for (let index = 0; index < 50; index += 1) {
    const page = root.querySelector("[data-om-table-page='2']");
    if (page) {
      page.click();
      return { failures: [], pageSize: firstPageSize };
    }
    await sleep(100);
  }
  return { failures: ["missing page 2 button after first page size selected"] };
})()
""",
        timeout=8.0,
    )
    pagination_result_failures = assertion_failures(pagination_result)
    result.pageErrors.extend(f"{path} pagination: {failure}" for failure in pagination_result_failures)
    if not pagination_result_failures:
        page_size = required_positive_integer_string(pagination_result, "pageSize", f"{path} pagination")
        wait_for_table_request(client, table_endpoint, {"page": "2", "page_size": page_size}, before, f"{path} pagination", result)


def assert_channel_names_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证频道名称页面的发布状态和绑定状态筛选只调用表格 API。"""
    for field_name, value, label in (
        ("published", "true", "channel names published filter"),
        ("bound_epg", "true", "channel names bound filter"),
    ):
        before = install_request_probe(client)
        clear_request_probe(client)
        filter_result = client.evaluate(
            r"""
(() => {
  const fieldName =
"""
            + json.dumps(field_name)
            + r""";
  const value =
"""
            + json.dumps(value)
            + r""";
  const failures = [];
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === "/channel-names/table");
  const form = Array.from(document.querySelectorAll("form[data-om-component='table-filter-form'][data-om-table-target]"))
    .find((candidate) => {
      const target = candidate.getAttribute("data-om-table-target");
      if (!target) return false;
      try {
        return document.querySelector(target) === root;
      } catch {
        return false;
      }
    });
  const field = form?.querySelector(`[name="${CSS.escape(fieldName)}"]`);
  if (!root) failures.push("missing channel names table root");
  if (!form) failures.push("missing channel names filter form");
  if (!field) failures.push(`missing channel names filter field: ${fieldName}`);
  if (failures.length) return { failures };
  const q = form.querySelector("[name='q']");
  if (q) q.value = "";
  field.value = value;
  field.dispatchEvent(new Event("change", { bubbles: true }));
  form.requestSubmit();
  return { failures };
})()
""",
            timeout=5.0,
        )
        filter_result_failures = assertion_failures(filter_result)
        result.pageErrors.extend(f"{label}: {failure}" for failure in filter_result_failures)
        if not filter_result_failures:
            wait_for_table_request(
                client,
                "/channel-names/table",
                {f"filter.{field_name}": value},
                before,
                label,
                result,
            )


def assert_catalog_channels_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证目录频道页面的状态筛选和置信度 slider 只调用表格 API。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    status_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === "/catalog-channels/table");
  const form = Array.from(document.querySelectorAll("form[data-om-component='table-filter-form'][data-om-table-target]"))
    .find((candidate) => {
      const target = candidate.getAttribute("data-om-table-target");
      if (!target) return false;
      try {
        return document.querySelector(target) === root;
      } catch {
        return false;
      }
    });
  const field = form?.querySelector("select[name='status']");
  if (!root) failures.push("missing catalog channels table root");
  if (!form) failures.push("missing catalog channels filter form");
  if (!field) failures.push("missing catalog channels status filter");
  if (failures.length) return { failures };
  const fieldRect = field.getBoundingClientRect();
  const wrapperRect = field.closest("[data-om-form-field]")?.getBoundingClientRect();
  const fieldStyle = getComputedStyle(field);
  if (field.id === "status") failures.push("catalog channels status filter uses reserved #status id");
  if (field.id !== "id_status") failures.push(`catalog channels status filter id is ${field.id || "(empty)"}`);
  if (fieldStyle.position === "absolute" || (wrapperRect && fieldRect.width < wrapperRect.width * 0.6)) {
    failures.push("catalog channels status filter is visually collapsed");
  }
  const option = Array.from(field.options).find((item) => item.value === "provisional") || Array.from(field.options).find((item) => item.value);
  if (!option) return { failures: ["missing non-empty status filter option"] };
  const q = form.querySelector("[name='q']");
  if (q) q.value = "";
  field.value = option.value;
  field.dispatchEvent(new Event("change", { bubbles: true }));
  form.requestSubmit();
  return { failures, value: option.value };
})()
""",
        timeout=5.0,
    )
    status_result_failures = assertion_failures(status_result)
    result.pageErrors.extend(f"catalog channels status filter: {failure}" for failure in status_result_failures)
    if not status_result_failures:
        status_value = required_success_string(status_result, "value", "catalog channels status filter")
        wait_for_table_request(
            client,
            "/catalog-channels/table",
            {"filter.status": status_value},
            before,
            "catalog channels status filter",
            result,
        )

    before = install_request_probe(client)
    clear_request_probe(client)
    slider_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const slider = document.querySelector("[data-om-component='slider'][data-om-slider-min-input='confidence_min']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#catalog-channels-table']");
  const min = form?.querySelector("[name='confidence_min']");
  const max = form?.querySelector("[name='confidence_max']");
  if (!slider) failures.push("missing catalog confidence slider");
  if (!slider?.noUiSlider) failures.push("catalog confidence slider not mounted");
  if (!form) failures.push("missing catalog filter form for slider");
  if (!min) failures.push("missing confidence_min input");
  if (!max) failures.push("missing confidence_max input");
  if (failures.length) return { failures };
  slider.noUiSlider.set([20, 80]);
  return { failures };
})()
""",
        timeout=5.0,
    )
    slider_result_failures = assertion_failures(slider_result)
    result.pageErrors.extend(f"catalog channels confidence slider: {failure}" for failure in slider_result_failures)
    if not slider_result_failures:
        wait_for_table_request(
            client,
            "/catalog-channels/table",
            {"filter.confidence_min": "20", "filter.confidence_max": "80"},
            before,
            "catalog channels confidence slider",
            result,
        )
        wait_for_table_success(client, "/catalog-channels/table", "catalog channels confidence slider", result)


def assert_preloader_idle(client: CDPClient, label: str, result: VerificationResult, timeout: float = 4.0) -> None:
    """等待页面级 preloader 进入空闲状态，并验证未残留遮挡。"""
    deadline = time.time() + timeout
    last_failures: list[str] = []

    while time.time() < deadline:
        preloader_result = client.evaluate(
            r"""
(() => {
  const failures = [];
  const root = document.querySelector("#preloader[data-om-component='preloader']");
  if (!root) return { failures: ["missing preloader root"] };
  const style = getComputedStyle(root);
  if (!root.hidden) failures.push("preloader is not hidden");
  if (root.getAttribute("aria-hidden") !== "true") failures.push("preloader aria-hidden is not true");
  if (root.getAttribute("data-om-status") !== "idle") failures.push("preloader is not idle");
  if (style.display !== "none") failures.push("preloader still affects layout");
  if (!root.querySelector("[data-om-preloader-status]")) failures.push("missing preloader status element");
  if (document.querySelector("#status")) failures.push("reserved #status element still exists");
  return { failures };
})()
""",
            timeout=2.0,
        )
        preloader_result_failures = assertion_failures(preloader_result)
        last_failures = [str(failure) for failure in preloader_result_failures]
        if not last_failures:
            return
        time.sleep(0.1)

    result.pageErrors.extend(f"{label} preloader: {failure}" for failure in last_failures)


def assert_preloader_critical_first_paint(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """验证 JS 和 Vite CSS 未启动前，HTML 内联关键样式已经让 preloader 覆盖首屏。"""
    failures: list[str] = []
    client.command("DOM.enable")
    client.command("CSS.enable")
    logout_browser_session(client, base_url)
    clear_browser_state(client, base_url)
    client.command("Emulation.setScriptExecutionDisabled", {"value": True})
    try:
        login_url = urllib.parse.urljoin(base_url, "/login")
        client.load_seen = False
        navigation_result = client.command("Page.navigate", {"url": login_url})
        error_text = navigation_result.get("errorText")
        if error_text:
            raise VerificationError(f"preloader critical first paint Page.navigate failed: {error_text}")
        try:
            client.wait_for_load(timeout=8.0)
        except PageLoadTimeout:
            client.pump(1.0)
        client.pump(0.5)

        document = client.command("DOM.getDocument", {"depth": -1, "pierce": True})
        document_root = document.get("root", {})
        document_url = document_root.get("documentURL")
        if not isinstance(document_url, str) or not same_origin_path_query(document_url, login_url):
            result.pageErrors.append(
                f"preloader critical first paint: document URL does not match login target ({document_url!r})"
            )
            return
        root_node_id = document_root.get("nodeId")
        if not root_node_id:
            result.pageErrors.append("preloader critical first paint: missing document root")
            return

        preloader_node_id = client.command(
            "DOM.querySelector",
            {"nodeId": root_node_id, "selector": "#preloader[data-om-component='preloader']"},
        ).get("nodeId", 0)
        if not preloader_node_id:
            result.pageErrors.append("preloader critical first paint: missing preloader root before JS")
            return

        attributes = client.command("DOM.getAttributes", {"nodeId": preloader_node_id}).get("attributes", [])
        attribute_map = dict(zip(attributes[0::2], attributes[1::2], strict=False))
        if "hidden" in attribute_map:
            failures.append("preloader is hidden before JS")

        computed = client.command("CSS.getComputedStyleForNode", {"nodeId": preloader_node_id}).get("computedStyle", [])
        style = {item.get("name"): item.get("value") for item in computed}
        if style.get("position") != "fixed":
            failures.append(f"preloader position is {style.get('position', '')}")
        if style.get("display") == "none":
            failures.append("preloader display is none before JS")
        if style.get("visibility") == "hidden":
            failures.append("preloader visibility is hidden before JS")
        if float(style.get("opacity") or "1") == 0:
            failures.append("preloader opacity is zero before JS")

        box_model = client.command("DOM.getBoxModel", {"nodeId": preloader_node_id}).get("model", {})
        border = box_model.get("border", [])
        viewport = client.command("Page.getLayoutMetrics").get("visualViewport", {})
        if len(border) >= 8:
            xs = border[0::2]
            ys = border[1::2]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            viewport_width = float(viewport.get("clientWidth") or 0)
            viewport_height = float(viewport.get("clientHeight") or 0)
            if width + 1 < viewport_width or height + 1 < viewport_height:
                failures.append("preloader is not covering viewport before JS")
        else:
            failures.append("preloader box model is unavailable before JS")

        spinner_node_id = client.command(
            "DOM.querySelector",
            {"nodeId": preloader_node_id, "selector": "[data-om-preloader-status]"},
        ).get("nodeId", 0)
        if not spinner_node_id:
            failures.append("missing preloader spinner before JS")
        else:
            spinner_computed = client.command("CSS.getComputedStyleForNode", {"nodeId": spinner_node_id}).get("computedStyle", [])
            spinner_style = {item.get("name"): item.get("value") for item in spinner_computed}
            if spinner_style.get("animation-name") == "none":
                failures.append("preloader spinner animation missing before JS")

        capture_named_screenshot(
            client,
            result,
            "preloader-critical-first-paint",
            "/tmp/oldman-preloader-critical-first-paint.png",
        )
    finally:
        client.command("Emulation.setScriptExecutionDisabled", {"value": False})

    result.pageErrors.extend(f"preloader critical first paint: {failure}" for failure in failures)


def assert_catalog_channel_edit_cancel_returns_to_list(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """验证 Cancel 优先历史后退，并保留进入编辑页前的列表查询状态。"""
    expected_query = "sort=channel_key&page_size=5"
    navigate(client, urllib.parse.urljoin(base_url, f"/catalog-channels?{expected_query}"))
    wait_for_table_ready(client, "/catalog-channels/table", "catalog channels cancel return", result, timeout=15.0)
    assert_preloader_idle(client, "catalog channels list", result)

    click_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("#catalog-channels-table[data-om-component='table']");
  const link = root?.querySelector('[data-om-table-row] a[href^="/catalog-channels/"][href$="/edit"]');
  if (!link) return { failures: ["missing catalog channels edit link"] };
  const href = new URL(link.href, location.href);
  link.click();
  return { failures, href: href.pathname };
})()
""",
        timeout=5.0,
    )
    click_result_failures = assertion_failures(click_result)
    result.pageErrors.extend(f"catalog channels cancel return: {failure}" for failure in click_result_failures)
    if not wait_for_path_pattern(client, r"^/catalog-channels/\d+/edit$", "catalog channels edit cancel", result):
        return
    assert_preloader_idle(client, "catalog channels edit", result)

    edit_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const cancel = Array.from(document.querySelectorAll("a,button")).find((item) => item.textContent.trim() === "Cancel");
  const back = Array.from(document.querySelectorAll("a")).find((item) => item.textContent.trim() === "Back");
  if (!cancel) failures.push("missing catalog channels edit cancel link");
  if (!back) failures.push("missing catalog channels edit back link");
  const cancelHref = cancel?.href || "";
  const backHref = back?.href || "";
  if (new URL(cancelHref, location.href).pathname !== "/catalog-channels") failures.push("catalog channels edit cancel does not target list page");
  if (new URL(backHref, location.href).pathname !== "/catalog-channels") failures.push("catalog channels edit back does not target list page");
  if (!cancel?.hasAttribute("data-om-history-back")) failures.push("catalog channels edit cancel is not history-aware");
  if (cancel?.getAttribute("data-om-history-mode") === "fallback") failures.push("catalog channels edit cancel forces fallback navigation");
  const restorationIndex = history.state?.turbo?.restorationIndex;
  if (typeof restorationIndex !== "number" || restorationIndex <= 0) failures.push("catalog channels edit has no restorable Turbo history");
  cancel?.click();
  return { failures, cancelHref, backHref, restorationIndex };
})()
""",
        timeout=5.0,
    )
    edit_result_failures = assertion_failures(edit_result)
    result.pageErrors.extend(f"catalog channels edit cancel: {failure}" for failure in edit_result_failures)
    if edit_result_failures:
        return

    wait_for_path(client, "/catalog-channels", "catalog channels cancel return", result)
    wait_for_table_ready(client, "/catalog-channels/table", "catalog channels cancel return", result)
    assert_preloader_idle(client, "catalog channels cancel return", result)
    restored_state = required_payload_object(
        client.evaluate(
            r"""
(() => ({
  query: new URLSearchParams(location.search).toString(),
  pageSize: document.querySelector("#catalog-channels-table [data-om-table-page-size-control]")?.value || "",
  sortDirection: document.querySelector('#catalog-channels-table [data-om-table-sort="channel_key"]')?.getAttribute("aria-sort") || ""
}))()
""",
            timeout=5.0,
        ),
        "catalog channels cancel restored state",
    )
    restored_query = required_string_field(restored_state, "query", "catalog channels cancel restored state")
    restored_page_size = required_string_field(restored_state, "pageSize", "catalog channels cancel restored state")
    restored_sort_direction = required_string_field(restored_state, "sortDirection", "catalog channels cancel restored state")
    if restored_query != expected_query:
        result.pageErrors.append(
            f"catalog channels cancel return: list query state was not restored ({restored_query!r})"
        )
    if restored_page_size != "5":
        result.pageErrors.append(
            f"catalog channels cancel return: page size state was not restored ({restored_page_size!r})"
        )
    if restored_sort_direction != "ascending":
        result.pageErrors.append(
            f"catalog channels cancel return: sort state was not restored ({restored_sort_direction!r})"
        )


def assert_catalog_channel_evidence_modal(client: CDPClient, result: VerificationResult) -> None:
    """验证目录频道行级 evidence modal 的打开、焦点、backdrop 和关闭行为。"""
    clear_transient_browser_overlays(client)
    modal_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const root = Array.from(document.querySelectorAll("[data-om-component='table'][data-om-table-src]"))
    .find((element) => new URL(element.getAttribute("data-om-table-src"), location.href).pathname === "/catalog-channels/table");
  const row = root?.querySelector("[data-om-table-row]");
  const dropdownToggle = row?.querySelector("[data-om-dropdown-toggle], .dropdown [data-bs-toggle='dropdown']");
  let targetSelector = "";
  const state = () => {
    const modal = targetSelector ? document.querySelector(targetSelector) : document.querySelector(".om-modal[id^='catalog-channel-evidence-'], .modal[id^='catalog-channel-evidence-']");
    const openModal = document.querySelector(".om-modal.is-open[id^='catalog-channel-evidence-'], .modal.show[id^='catalog-channel-evidence-']");
    return {
      hasRoot: Boolean(root),
      hasRow: Boolean(row),
      hasDropdownToggle: Boolean(dropdownToggle),
      modalCount: document.querySelectorAll(".om-modal[id^='catalog-channel-evidence-'], .modal[id^='catalog-channel-evidence-']").length,
      modalManaged: modal?.getAttribute("data-om-modal-managed") || "",
      modalState: modal?.getAttribute("data-om-state") || "",
      modalHidden: modal?.hidden ?? null,
      modalClass: modal?.className || "",
      targetSelector,
      openModal: Boolean(openModal),
      backdropCount: document.querySelectorAll(".om-modal-backdrop, .modal-backdrop.show").length,
      activeTag: document.activeElement?.tagName || ""
    };
  };
  if (!root) failures.push("missing catalog channels table root");
  if (!row) failures.push("missing catalog channels row");
  if (!dropdownToggle) failures.push("missing catalog channel action dropdown");
  if (failures.length) return { failures, state: state() };

  dropdownToggle.click();
  await sleep(120);
  const evidenceAction = row.querySelector("[data-om-modal-target^='#catalog-channel-evidence-']");
  if (!evidenceAction) return { failures: ["missing catalog channel evidence action"], state: state() };
  targetSelector = evidenceAction.getAttribute("data-om-modal-target") || "";
  evidenceAction.click();
  for (let index = 0; index < 40 && !document.querySelector(".om-modal.is-open[id^='catalog-channel-evidence-'], .modal.show[id^='catalog-channel-evidence-']"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector(".om-modal.is-open[id^='catalog-channel-evidence-'], .modal.show[id^='catalog-channel-evidence-']");
  const backdrop = document.querySelector(".om-modal-backdrop, .modal-backdrop.show");
  if (!visible(modal)) failures.push("evidence modal is not visible");
  if (!visible(backdrop)) failures.push("evidence modal backdrop is not visible");
  if (modal && !modal.contains(document.activeElement)) failures.push("evidence modal did not keep focus inside modal");
  if (modal && !modal.textContent.includes("Evidence")) failures.push("evidence modal title/content missing");
  const close = modal?.querySelector("[data-om-modal-close], [data-bs-dismiss='modal']");
  if (!close) failures.push("missing evidence modal close button");
  if (failures.length) return { failures, state: state() };

  close.click();
  const isClosing = () => {
    const current = targetSelector ? document.querySelector(targetSelector) : modal;
    const stillVisible = Boolean(current && (!current.hidden || current.getAttribute("data-om-state") === "closing"));
    return stillVisible || Boolean(document.querySelector(".om-modal-backdrop, .modal-backdrop.show"));
  };
  for (let index = 0; index < 40 && isClosing(); index += 1) {
    await sleep(100);
  }
  const closedModal = targetSelector ? document.querySelector(targetSelector) : modal;
  if (closedModal && (!closedModal.hidden || closedModal.getAttribute("data-om-state") !== "closed")) failures.push("evidence modal did not close");
  if (document.querySelector(".om-modal-backdrop, .modal-backdrop.show")) failures.push("evidence modal backdrop did not close");
  return { failures, state: state() };
})()
""",
        timeout=10.0,
    )
    modal_result_failures = assertion_failures(modal_result)
    result.pageErrors.extend(f"catalog channel evidence modal: {failure}" for failure in modal_result_failures)
    if modal_result_failures:
        result.pageErrors.append(f"catalog channel evidence modal state: {modal_result.get('state')}")


def assert_catalog_feed_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证 CatalogFeed 列表筛选、datetime picker、logo/default/status 可视元素。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/catalog-feeds/table']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#catalog-feeds-table']");
  if (!root) failures.push("missing catalog feeds table root");
  if (!form) failures.push("missing catalog feeds filter form");
  if (!root?.querySelector(".om-avatar-xs, .om-avatar-sm, .avatar-xs, img[alt*='logo' i]")) failures.push("missing catalog feed logo preview or placeholder");
  if (!root?.textContent.includes("Default") && !root?.textContent.includes("Variant")) failures.push("missing catalog feed default marker");
  if (!root?.querySelector(".om-badge, .badge")) failures.push("missing catalog feed status badge");
  for (const name of ["created_from", "created_to", "updated_from", "updated_to"]) {
    const input = form?.querySelector(`[name="${name}"]`);
    if (!input) {
      failures.push(`missing datetime filter ${name}`);
      continue;
    }
    if (input.getAttribute("type") !== "text") failures.push(`${name} type is ${input.getAttribute("type") || ""}`);
    if (input.getAttribute("data-om-component") !== "date-time-picker") failures.push(`${name} missing date-time-picker component`);
    if (input.getAttribute("data-provider") !== "flatpickr") failures.push(`${name} missing flatpickr provider`);
    if (!input.hasAttribute("data-enable-time")) failures.push(`${name} missing enable-time`);
    if (input.getAttribute("data-om-component-state") !== "mounted") failures.push(`${name} picker not mounted`);
    if (!input._flatpickr) failures.push(`${name} flatpickr instance missing`);
    if (input._flatpickr && input._flatpickr.config.enableTime !== true) failures.push(`${name} flatpickr enableTime is not true`);
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"catalog feeds filters: {failure}" for failure in component_result_failures)

    before = install_request_probe(client)
    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#catalog-feeds-table']");
  if (!form) return { failures: ["missing catalog feeds filter form"] };
  const values = {
    status: "provisional",
    is_default: "true",
    has_logo: "false",
    created_from: "2026-01-01T00:00",
    updated_to: "2026-12-31T23:59"
  };
  for (const [name, value] of Object.entries(values)) {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) {
      failures.push(`missing filter field ${name}`);
      continue;
    }
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const q = form.querySelector("[name='q']");
  if (q) q.value = "";
  if (failures.length) return { failures };
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"catalog feeds filter submit: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        wait_for_table_request(
            client,
            "/catalog-feeds/table",
            {
                "filter.status": "provisional",
                "filter.is_default": "true",
                "filter.has_logo": "false",
                "filter.created_from": "2026-01-01T00:00",
                "filter.updated_to": "2026-12-31T23:59",
            },
            before,
            "catalog feeds filters",
            result,
        )


def assert_upstream_record_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证上游记录筛选表单只调用表格 API，并覆盖 autocomplete 和 datetime 字段。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/upstream-records/table']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#upstream-records-table']");
  if (!root) failures.push("missing upstream records table root");
  if (!form) failures.push("missing upstream records filter form");
  if (!form?.querySelector("[data-om-component='autocomplete']")) failures.push("missing catalog feed autocomplete");
  for (const name of ["last_seen_from", "last_seen_to"]) {
    const input = form?.querySelector(`[name="${name}"]`);
    if (!input) {
      failures.push(`missing datetime filter ${name}`);
      continue;
    }
    if (input.getAttribute("data-om-component") !== "date-time-picker") failures.push(`${name} missing date-time-picker component`);
    if (input.getAttribute("data-provider") !== "flatpickr") failures.push(`${name} missing flatpickr provider`);
    if (!input.hasAttribute("data-enable-time")) failures.push(`${name} missing enable-time`);
    if (input.getAttribute("data-om-component-state") !== "mounted") failures.push(`${name} picker not mounted`);
    if (!input._flatpickr) failures.push(`${name} flatpickr instance missing`);
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"upstream records filters: {failure}" for failure in component_result_failures)

    before = install_request_probe(client)
    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#upstream-records-table']");
  if (!form) return { failures: ["missing upstream records filter form"] };
  const values = {
    status: "active",
    source_code: "browser-gate",
    record_kind: "channel",
    last_seen_from: "2026-01-01T00:00",
    last_seen_to: "2026-12-31T23:59"
  };
  for (const [name, value] of Object.entries(values)) {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) {
      failures.push(`missing filter field ${name}`);
      continue;
    }
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const q = form.querySelector("[name='q']");
  if (q) q.value = "";
  if (failures.length) return { failures };
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"upstream records filter submit: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        wait_for_table_request(
            client,
            "/upstream-records/table",
            {
                "filter.status": "active",
                "filter.source_code": "browser-gate",
                "filter.record_kind": "channel",
                "filter.last_seen_from": "2026-01-01T00:00",
                "filter.last_seen_to": "2026-12-31T23:59",
            },
            before,
            "upstream records filters",
            result,
        )


def assert_upstream_record_local_list(client: CDPClient, result: VerificationResult) -> None:
    """验证上游审计页右侧本地 list 组件可搜索、排序和分页。"""
    list_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='list']");
  if (!root) return { failures: ["missing upstream local list root"] };
  if (root.getAttribute("data-om-component-state") !== "mounted") failures.push("upstream local list not mounted");
  const items = Array.from(root.querySelectorAll("[data-om-list-item]"));
  if (!items.length) failures.push("upstream local list has no items");
  const search = root.querySelector("[data-om-list-search]");
  if (!search) failures.push("missing upstream local list search input");
  const sort = root.querySelector("[data-om-list-sort='count']");
  if (!sort) failures.push("missing upstream local list count sort");
  if (failures.length) return { failures };

  const firstText = items[0].querySelector(".name")?.textContent?.trim() || "";
  search.value = "__definitely_no_match__";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  const hiddenAfterMiss = items.filter((item) => item.hidden).length;
  const emptyVisible = root.querySelector("[data-om-list-empty]")?.hidden === false;
  if (!hiddenAfterMiss) failures.push("local list search did not hide items");
  if (!emptyVisible) failures.push("local list empty state did not show");

  search.value = firstText.slice(0, Math.max(1, Math.min(4, firstText.length)));
  search.dispatchEvent(new Event("input", { bubbles: true }));
  if (!items.some((item) => !item.hidden)) failures.push("local list search did not restore matching items");
  sort.click();
  if (!sort.getAttribute("aria-sort")) failures.push("local list sort did not set aria-sort");
  return { failures };
})()
""",
        timeout=5.0,
    )
    list_result_failures = assertion_failures(list_result)
    result.pageErrors.extend(f"upstream local list: {failure}" for failure in list_result_failures)


def assert_upstream_record_raw_modal(client: CDPClient, result: VerificationResult) -> None:
    """验证上游记录行级 raw payload modal 可打开和关闭。"""
    clear_transient_browser_overlays(client)
    before = install_request_probe(client)
    clear_request_probe(client)
    modal_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/upstream-records/table']");
  const row = root?.querySelector("[data-om-table-row]");
  const dropdownToggle = row?.querySelector("[data-om-dropdown-toggle], .dropdown [data-bs-toggle='dropdown']");
  if (!root) failures.push("missing upstream records table root");
  if (!row) failures.push("missing upstream records row");
  if (!dropdownToggle) failures.push("missing upstream row action dropdown");
  if (failures.length) return { failures };

  dropdownToggle.click();
  await sleep(120);
  const rawAction = row.querySelector("[data-om-modal-target='#upstream-record-raw-modal'][data-om-modal-url]");
  if (!rawAction) return { failures: ["missing upstream raw payload action"] };
  const modalUrl = new URL(rawAction.getAttribute("data-om-modal-url") || "", location.href);
  rawAction.click();
  for (let index = 0; index < 40 && !document.querySelector("#upstream-record-raw-modal.is-open[data-om-status='success'], #upstream-record-raw-modal.show[data-om-status='success']"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#upstream-record-raw-modal.is-open, #upstream-record-raw-modal.show");
  const backdrop = document.querySelector(".om-modal-backdrop, .modal-backdrop.show");
  if (!visible(modal)) failures.push("raw payload modal is not visible");
  if (!visible(backdrop)) failures.push("raw payload modal backdrop is not visible");
  if (modal && !modal.textContent.includes("Raw Payload")) failures.push("raw payload modal title/content missing");
  if (modal && !modal.querySelector("pre")) failures.push("raw payload modal did not load remote pre content");
  const close = modal?.querySelector("[data-om-modal-close], [data-bs-dismiss='modal']");
  if (!close) failures.push("missing raw payload modal close button");
  if (failures.length) return { failures, modalPath: modalUrl.pathname };

  close.click();
  const isClosing = () => {
    const current = document.querySelector("#upstream-record-raw-modal");
    const stillVisible = Boolean(current && (!current.hidden || current.getAttribute("data-om-state") === "closing"));
    return stillVisible || Boolean(document.querySelector(".om-modal-backdrop, .modal-backdrop.show"));
  };
  for (let index = 0; index < 40 && isClosing(); index += 1) {
    await sleep(100);
  }
  const closedModal = document.querySelector("#upstream-record-raw-modal");
  if (closedModal && (!closedModal.hidden || closedModal.getAttribute("data-om-state") !== "closed")) failures.push("raw payload modal did not close");
  if (document.querySelector(".om-modal-backdrop, .modal-backdrop.show")) failures.push("raw payload modal backdrop did not close");
  return { failures, modalPath: modalUrl.pathname };
})()
""",
        timeout=10.0,
    )
    modal_result_failures = assertion_failures(modal_result)
    result.pageErrors.extend(f"upstream raw modal: {failure}" for failure in modal_result_failures)
    if not modal_result_failures:
        modal_path = required_success_route(
            modal_result,
            "modalPath",
            r"/upstream-records/[0-9]+/raw-modal",
            "upstream raw modal",
        )
        wait_for_endpoint_request(client, modal_path, {}, before, "upstream raw modal", result, noun="raw payload modal 请求")


def assert_upstream_catalog_feed_autocomplete_filter(client: CDPClient, result: VerificationResult) -> None:
    """验证 CatalogFeed autocomplete 选中值会进入上游记录表格筛选请求。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    select_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const form = document.querySelector("form[data-om-table-target='#upstream-records-table']");
  const root = form?.querySelector("[data-om-component='autocomplete']");
  const input = root?.querySelector("[data-om-autocomplete-input]");
  const hidden = root?.querySelector("[data-om-autocomplete-value-control]");
  if (!form) failures.push("missing upstream records filter form");
  if (!root) failures.push("missing CatalogFeed autocomplete root");
  if (!input) failures.push("missing CatalogFeed autocomplete input");
  if (!hidden) failures.push("missing CatalogFeed autocomplete hidden value control");
  if (failures.length) return { failures };

  for (const field of form.querySelectorAll("input[name], select[name]")) {
    if (field.name !== "catalog_feed_id") {
      field.value = "";
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
  input.focus();
  input.value = "a";
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "a" }));
  for (let index = 0; index < 40 && !root.querySelector("[data-om-autocomplete-item]"); index += 1) {
    await sleep(100);
  }
  const item = root.querySelector("[data-om-autocomplete-item]");
  if (!item) return { failures: ["CatalogFeed autocomplete rendered no selectable item"] };
  item.click();
  await sleep(100);
  const value = hidden.value || "";
  if (!value) failures.push("CatalogFeed autocomplete hidden value stayed empty after selection");
  form.requestSubmit();
  return { failures, value };
})()
""",
        timeout=8.0,
    )
    select_result_failures = assertion_failures(select_result)
    result.pageErrors.extend(f"upstream catalog feed filter: {failure}" for failure in select_result_failures)
    if select_result_failures:
        return
    value = required_success_string(select_result, "value", "upstream catalog feed filter")
    wait_for_endpoint_request(client, "/admin/select/catalog_feeds", {"q": "a"}, before, "upstream catalog feed autocomplete", result, noun="远程 Autocomplete 搜索请求")
    wait_for_table_request(client, "/upstream-records/table", {"filter.catalog_feed_id": value}, before, "upstream catalog feed filter", result)


def assert_logo_asset_charts(client: CDPClient, result: VerificationResult) -> None:
    """验证 Logo 工作台三张业务图表真实挂载。"""
    for label, path in (
        ("logo asset quality chart", "/logo-assets/charts/quality-distribution"),
        ("logo asset mime chart", "/logo-assets/charts/mime-distribution"),
        ("logo asset dimensions chart", "/logo-assets/charts/dimensions"),
    ):
        assert_dashboard_chart(client, result, label, path)


def assert_logo_asset_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证 Logo 工作台质量 slider 会写回筛选表单并刷新表格。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/logo-assets/table']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#logo-assets-table']");
  const slider = document.querySelector("[data-om-component='slider'][data-om-slider-min-input='quality_min']");
  if (!root) failures.push("missing logo assets table root");
  if (!form) failures.push("missing logo assets filter form");
  if (!slider) failures.push("missing logo quality slider");
  if (!slider?.noUiSlider) failures.push("logo quality slider not mounted");
  if (!form?.querySelector("[name='quality_min']")) failures.push("missing quality_min field");
  if (!form?.querySelector("[name='quality_max']")) failures.push("missing quality_max field");
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"logo assets filters: {failure}" for failure in component_result_failures)
    if component_result_failures:
        return

    before = install_request_probe(client)
    clear_request_probe(client)
    slider_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const slider = document.querySelector("[data-om-component='slider'][data-om-slider-min-input='quality_min']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#logo-assets-table']");
  slider.noUiSlider.set([25, 90]);
  await sleep(100);
  const min = form.querySelector("[name='quality_min']")?.value || "";
  const max = form.querySelector("[name='quality_max']")?.value || "";
  if (min !== "25") failures.push(`quality_min was ${min}`);
  if (max !== "90") failures.push(`quality_max was ${max}`);
  return { failures, min, max };
})()
""",
        timeout=5.0,
    )
    slider_result_failures = assertion_failures(slider_result)
    result.pageErrors.extend(f"logo assets quality slider: {failure}" for failure in slider_result_failures)
    if not slider_result_failures:
        wait_for_table_request(
            client,
            "/logo-assets/table",
            {"filter.quality_min": "25", "filter.quality_max": "90"},
            before,
            "logo assets quality slider",
            result,
        )
        wait_for_table_success(client, "/logo-assets/table", "logo assets quality slider", result)


def assert_logo_catalog_feed_autocomplete_filter(client: CDPClient, result: VerificationResult) -> None:
    """验证 Logo 工作台 CatalogFeed autocomplete 选中值会进入表格筛选请求。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    select_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const form = document.querySelector("form[data-om-table-target='#logo-assets-table']");
  const root = form?.querySelector("[data-om-component='autocomplete']");
  const input = root?.querySelector("[data-om-autocomplete-input]");
  const hidden = root?.querySelector("[data-om-autocomplete-value-control]");
  if (!form) failures.push("missing logo assets filter form");
  if (!root) failures.push("missing Logo CatalogFeed autocomplete root");
  if (!input) failures.push("missing Logo CatalogFeed autocomplete input");
  if (!hidden) failures.push("missing Logo CatalogFeed hidden value control");
  if (failures.length) return { failures };

  for (const field of form.querySelectorAll("input[name], select[name]")) {
    if (field.name !== "catalog_feed_id") {
      field.value = "";
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
  input.focus();
  input.value = "a";
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: "a" }));
  for (let index = 0; index < 40 && !root.querySelector("[data-om-autocomplete-item]"); index += 1) {
    await sleep(100);
  }
  const item = root.querySelector("[data-om-autocomplete-item]");
  if (!item) return { failures: ["Logo CatalogFeed autocomplete rendered no selectable item"] };
  item.click();
  await sleep(100);
  const value = hidden.value || "";
  if (!value) failures.push("Logo CatalogFeed autocomplete hidden value stayed empty after selection");
  form.requestSubmit();
  return { failures, value };
})()
""",
        timeout=8.0,
    )
    select_result_failures = assertion_failures(select_result)
    result.pageErrors.extend(f"logo catalog feed filter: {failure}" for failure in select_result_failures)
    if select_result_failures:
        return
    value = required_success_string(select_result, "value", "logo catalog feed filter")
    wait_for_endpoint_request(client, "/admin/select/catalog_feeds", {"q": "a"}, before, "logo catalog feed autocomplete", result, noun="远程 Autocomplete 搜索请求")
    wait_for_table_request(client, "/logo-assets/table", {"filter.catalog_feed_id": value}, before, "logo catalog feed filter", result)


def assert_logo_asset_thumbnail_column(client: CDPClient, result: VerificationResult) -> None:
    """验证 Logo 缩略图列不会撑破表格，图片都有 alt。"""
    thumbnail_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/logo-assets/table']");
  const table = root?.querySelector("table");
  const rows = Array.from(root?.querySelectorAll("[data-om-table-row]") || []);
  if (!root) failures.push("missing logo table root");
  if (!table) failures.push("missing logo table element");
  if (!rows.length) failures.push("logo table has no rows for thumbnail gate");
  for (const img of root?.querySelectorAll("tbody img") || []) {
    const rect = img.getBoundingClientRect();
    if (!img.getAttribute("alt")) failures.push("logo thumbnail missing alt");
    if (rect.width > 80 || rect.height > 80) failures.push(`logo thumbnail too large ${rect.width}x${rect.height}`);
  }
  return {
    failures,
    rowCount: rows.length,
    tableWidth: table?.getBoundingClientRect().width || 0,
    scrollWidth: table?.scrollWidth || 0,
  };
})()
""",
        timeout=5.0,
    )
    thumbnail_result_failures = assertion_failures(thumbnail_result)
    result.pageErrors.extend(f"logo thumbnail column: {failure}" for failure in thumbnail_result_failures)


def assert_logo_asset_compare_modal(client: CDPClient, result: VerificationResult) -> None:
    """验证 Logo 行级图片对比 modal 通过远程 endpoint 加载。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    modal_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/logo-assets/table']");
  const row = root?.querySelector("[data-om-table-row]");
  const dropdownToggle = row?.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']");
  if (!root) failures.push("missing logo assets table root");
  if (!row) failures.push("missing logo assets row");
  if (!dropdownToggle) failures.push("missing logo row action dropdown");
  if (failures.length) return { failures };
  dropdownToggle.click();
  await sleep(100);
  const compareAction = row.querySelector("[data-om-modal-target='#logo-asset-compare-modal'][data-om-modal-url]");
  if (!compareAction) return { failures: ["missing logo compare action"] };
  const modalPath = new URL(compareAction.getAttribute("data-om-modal-url"), location.href).pathname;
  compareAction.click();
  for (let index = 0; index < 40 && !document.querySelector("#logo-asset-compare-modal.is-open[data-om-status='success'], #logo-asset-compare-modal.show[data-om-status='success']"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#logo-asset-compare-modal.is-open, #logo-asset-compare-modal.show");
  if (!modal) failures.push("logo compare modal did not open");
  if (!modal?.querySelector(".om-modal-body, .modal-body")) failures.push("logo compare modal missing body");
  const images = Array.from(modal?.querySelectorAll(".om-modal-body img, .modal-body img") || []);
  for (const image of images) {
    if (!image.getAttribute("alt")) failures.push("logo compare image missing alt");
  }
  if (!images.length && !modal?.textContent.includes("Image unavailable")) failures.push("logo compare modal rendered neither images nor fallbacks");
  modal?.querySelector("[data-om-modal-close], [data-bs-dismiss='modal']")?.click();
  for (let index = 0; index < 40 && document.querySelector("#logo-asset-compare-modal.is-open, #logo-asset-compare-modal.show"); index += 1) {
    await sleep(50);
  }
  if (document.querySelector("#logo-asset-compare-modal.is-open, #logo-asset-compare-modal.show")) failures.push("logo compare modal did not close");
  return { failures, modalPath };
})()
""",
        timeout=10.0,
    )
    modal_result_failures = assertion_failures(modal_result)
    result.pageErrors.extend(f"logo compare modal: {failure}" for failure in modal_result_failures)
    if not modal_result_failures:
        modal_path = required_success_route(
            modal_result,
            "modalPath",
            r"/logo-assets/[0-9]+/compare-modal",
            "logo compare modal",
        )
        wait_for_endpoint_request(client, modal_path, {}, before, "logo compare modal", result, noun="logo compare modal 请求")


def assert_match_decision_filter_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证人工匹配决策筛选表单只调用表格 API。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/match-decisions/table']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#match-decisions-table']");
  if (!root) failures.push("missing match decisions table root");
  if (!form) failures.push("missing match decisions filter form");
  if (!form?.querySelector("[name='decided_by']")) failures.push("missing decided_by operator filter");
  for (const name of ["source_record_id", "catalog_channel_id", "catalog_feed_id"]) {
    const fieldRoot = form?.querySelector(`[name="${name}"]`)?.closest("[data-om-component='autocomplete']");
    if (!fieldRoot) failures.push(`missing autocomplete filter ${name}`);
    if (fieldRoot?.getAttribute("data-om-component-state") !== "mounted") failures.push(`${name} autocomplete not mounted`);
  }
  if (!document.querySelector("#match-decisions-feedback[data-om-component='feedback']")) failures.push("missing match decisions feedback component");
  if (!document.querySelector("#match-decision-edit-modal[data-om-component='modal']")) failures.push("missing match decision modal");
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"match decisions filters: {failure}" for failure in component_result_failures)

    before = install_request_probe(client)
    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#match-decisions-table']");
  if (!form) return { failures: ["missing match decisions filter form"] };
  const decision = form.querySelector("[name='decision']");
  const scope = form.querySelector("[name='decision_scope']");
  const operator = form.querySelector("[name='decided_by']");
  const q = form.querySelector("[name='q']");
  if (!decision) failures.push("missing filter.decision field");
  if (!scope) failures.push("missing decision_scope field");
  if (!operator) failures.push("missing decided_by field");
  if (failures.length) return { failures };
  if (q) q.value = "";
  decision.value = "accepted";
  decision.dispatchEvent(new Event("change", { bubbles: true }));
  scope.value = "feed";
  scope.dispatchEvent(new Event("input", { bubbles: true }));
  operator.value = "browser-gate";
  operator.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"match decisions filter submit: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        wait_for_table_request(
            client,
            "/match-decisions/table",
            {"filter.decision": "accepted", "filter.decision_scope": "feed", "filter.decided_by": "browser-gate"},
            before,
            "match decisions filters",
            result,
        )
        wait_for_table_refresh_complete(client, "/match-decisions/table", "match decisions filters", result)

    clear_request_probe(client)
    autocomplete_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#match-decisions-table']");
  if (!form) return { failures: ["missing match decisions filter form"] };
  for (const element of form.querySelectorAll("input, select")) {
    element.value = "";
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const choose = async (name, query) => {
    const value = form.querySelector(`[name="${name}"]`);
    const root = value?.closest("[data-om-component='autocomplete']");
    const input = root?.querySelector("[data-om-autocomplete-input]");
    if (!root || !input || !value) {
      failures.push(`missing autocomplete ${name}`);
      return "";
    }
    input.focus();
    input.value = query;
    input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: query }));
    for (let index = 0; index < 50 && !root.querySelector("[data-om-autocomplete-item]"); index += 1) {
      await sleep(100);
    }
    const item = root.querySelector("[data-om-autocomplete-item]");
    if (!item) {
      failures.push(`${name} autocomplete did not render suggestions`);
      return "";
    }
    item.click();
    await sleep(50);
    if (!value.value) failures.push(`${name} autocomplete did not write hidden value`);
    if (!input.dataset.omAutocompleteValue) failures.push(`${name} autocomplete did not write visible value dataset`);
    return value.value;
  };

  const sourceRecordId = await choose("source_record_id", "Browser Gate Match");
  const catalogChannelId = await choose("catalog_channel_id", "Browser Gate Match");
  const catalogFeedId = await choose("catalog_feed_id", "Browser Gate Match");
  if (failures.length) return { failures };
  form.requestSubmit();
  return { failures, sourceRecordId, catalogChannelId, catalogFeedId };
})()
""",
        timeout=18.0,
    )
    autocomplete_result_failures = assertion_failures(autocomplete_result)
    result.pageErrors.extend(f"match decisions autocomplete filters: {failure}" for failure in autocomplete_result_failures)
    if not autocomplete_result_failures:
        source_record_id = required_success_string(
            autocomplete_result,
            "sourceRecordId",
            "match decisions autocomplete filters",
        )
        catalog_channel_id = required_success_string(
            autocomplete_result,
            "catalogChannelId",
            "match decisions autocomplete filters",
        )
        catalog_feed_id = required_success_string(
            autocomplete_result,
            "catalogFeedId",
            "match decisions autocomplete filters",
        )
        wait_for_endpoint_request(client, "/admin/select/upstream_records", {"q": "Browser Gate Match"}, before, "match decisions source record autocomplete", result, noun="上游记录 Autocomplete 搜索请求")
        wait_for_endpoint_request(client, "/admin/select/catalog_channels", {"q": "Browser Gate Match"}, before, "match decisions catalog channel autocomplete", result, noun="CatalogChannel Autocomplete 搜索请求")
        wait_for_endpoint_request(client, "/admin/select/catalog_feeds", {"q": "Browser Gate Match"}, before, "match decisions catalog feed autocomplete", result, noun="CatalogFeed Autocomplete 搜索请求")
        wait_for_table_request(
            client,
            "/match-decisions/table",
            {
                "filter.source_record_id": source_record_id,
                "filter.catalog_channel_id": catalog_channel_id,
                "filter.catalog_feed_id": catalog_feed_id,
            },
            before,
            "match decisions autocomplete filters",
            result,
        )
        wait_for_table_refresh_complete(client, "/match-decisions/table", "match decisions autocomplete filters", result)


def assert_match_decision_modal_edit(client: CDPClient, result: VerificationResult) -> None:
    """验证人工匹配决策 Modal 中的普通 Form 非法和合法提交。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    modal_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const root = document.querySelector("[data-om-component='table'][data-om-table-src='/match-decisions/table']");
  const row = root?.querySelector("[data-om-table-row]");
  const dropdownToggle = row?.querySelector("[data-om-dropdown-toggle], .dropdown [data-bs-toggle='dropdown']");
  if (!root) failures.push("missing match decisions table root");
  if (!row) failures.push("missing match decisions row");
  if (!dropdownToggle) failures.push("missing match decisions row action dropdown");
  if (failures.length) return { failures };

  dropdownToggle.click();
  await sleep(120);
  const editAction = row.querySelector("[data-om-modal-target='#match-decision-edit-modal'][data-om-modal-url]");
  if (!editAction) return { failures: ["missing match decision edit action"] };
  const modalUrl = new URL(editAction.getAttribute("data-om-modal-url") || "", location.href);
  editAction.click();
  for (let index = 0; index < 50 && !document.querySelector("#match-decision-edit-modal.is-open form[data-om-form], #match-decision-edit-modal.show form[data-om-form]"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#match-decision-edit-modal.is-open, #match-decision-edit-modal.show");
  const form = modal?.querySelector("form[data-om-form]");
  if (!visible(modal)) failures.push("match decision edit modal is not visible");
  if (!form) failures.push("match decision edit modal did not load form");
  if (form?.getAttribute("data-om-component") !== "form") failures.push("match decision form is not mounted as form");
  if (!form?.querySelector("[data-om-component='form-validator']")) failures.push("match decision form validator is missing");
  if (!form?.querySelector("[name='decision']")) failures.push("match decision form missing decision field");
  if (!form?.querySelector("[name='reason']")) failures.push("match decision form missing reason field");
  const visibleFooters = Array.from(modal?.querySelectorAll(".om-modal-footer, .modal-footer") || []).filter(visible);
  if (visibleFooters.length !== 1) failures.push("match decision modal has duplicate visible footers");
  if (failures.length) return { failures, modalPath: modalUrl.pathname };

  const actionPath = new URL(form.getAttribute("action") || "", location.href).pathname;
  const reason = form.querySelector("[name='reason']");
  const originalReason = reason.value || "Browser gate decision";
  let invalidEventCount = 0;
  form.addEventListener("om:form-validator:invalid", () => { invalidEventCount += 1; });
  reason.value = "";
  reason.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  await sleep(250);
  if (!form.classList.contains("was-validated")) failures.push("match decision reason native validation did not mark form");
  if (invalidEventCount < 1) failures.push("match decision reason native validation did not emit invalid event");
  if (reason.checkValidity()) failures.push("match decision reason native validation unexpectedly passed");
  if (!visible(modal)) failures.push("match decision modal closed after invalid submit");
  const invalidPosted = (window.__oldmanRequestLog || []).some((request) => {
    const url = new URL(request.url, location.href);
    return url.pathname === actionPath && String(request.method || "GET").toUpperCase() === "POST";
  });
  if (invalidPosted) failures.push("match decision invalid submit posted despite native validation");
  if (failures.length) return { failures, modalPath: modalUrl.pathname, actionPath };

  reason.value = `${originalReason} gate`;
  reason.dispatchEvent(new Event("input", { bubbles: true }));
  const decision = form.querySelector("[name='decision']");
  if (decision && !decision.value) {
    const option = Array.from(decision.options).find((item) => item.value);
    if (option) decision.value = option.value;
  }
  decision?.dispatchEvent(new Event("change", { bubbles: true }));
  form.requestSubmit();
  for (let index = 0; index < 60 && document.querySelector("#match-decision-edit-modal.is-open, #match-decision-edit-modal.show"); index += 1) {
    await sleep(100);
  }
  if (document.querySelector("#match-decision-edit-modal.is-open, #match-decision-edit-modal.show")) failures.push("match decision valid submit did not close modal");
  for (let index = 0; index < 50 && !document.querySelector(".toastify.om-toast.on"); index += 1) {
    await sleep(100);
  }
  const successToast = document.querySelector(".toastify.om-toast.on");
  if (!successToast?.textContent.includes("Decision saved")) failures.push("match decision valid submit did not show feedback toast");
  return { failures, modalPath: modalUrl.pathname, actionPath };
})()
""",
        timeout=18.0,
    )
    modal_result_failures = assertion_failures(modal_result)
    result.pageErrors.extend(f"match decision modal: {failure}" for failure in modal_result_failures)
    if not modal_result_failures:
        modal_path = required_success_route(
            modal_result,
            "modalPath",
            r"/match-decisions/[0-9]+/edit-modal",
            "match decision modal",
        )
        action_path = required_success_route(
            modal_result,
            "actionPath",
            r"/match-decisions/[0-9]+/edit",
            "match decision modal",
        )
        if modal_path.removesuffix("-modal") != action_path:
            raise VerificationError("match decision modal success payload routes use different records")
        wait_for_endpoint_request(client, modal_path, {}, before, "match decision edit modal", result, noun="match decision modal 请求")
        wait_for_endpoint_request(client, action_path, {}, before, "match decision valid submit", result, noun="match decision valid submit 请求")
        wait_for_endpoint_request(client, "/match-decisions/table", {}, before, "match decision valid submit table reload", result, noun="match decision table reload 请求")


def assert_users_management_interactions(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """验证 Users 管理页筛选、密码 modal、启停保护和删除保护。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const table = document.querySelector("[data-om-component='table'][data-om-table-src='/users/table']");
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#users-table']");
  if (!table) failures.push("missing users table");
  if (table?.getAttribute("data-om-table-format") !== "json") failures.push("users table is not using JSON mode");
  if (!form) failures.push("missing users filter form");
  if (!form?.querySelector("[name='is_active']")) failures.push("missing users is_active filter");
  if (!form?.querySelector("[name='is_superuser']")) failures.push("missing users is_superuser filter");
  if (!document.querySelector("#users-feedback[data-om-component='feedback']")) failures.push("missing users feedback");
  for (const selector of ["#user-password-modal", "#user-status-modal", "#user-delete-modal"]) {
    if (!document.querySelector(`${selector}[data-om-component='modal']`)) failures.push(`missing ${selector}`);
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"users components: {failure}" for failure in component_result_failures)
    if component_result_failures:
        return

    rapid_search_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const finalQuery =
"""
        + json.dumps(DEFAULT_USERNAME)
        + r""";
  const table = document.querySelector("#users-table");
  const form = document.querySelector("form[data-om-table-target='#users-table']");
  const q = form?.querySelector("[name='q']");
  const active = form?.querySelector("[name='is_active']");
  const superuser = form?.querySelector("[name='is_superuser']");
  if (!table || !form || !q || !active || !superuser) return { failures: ["missing users rapid-search controls"] };

  let refreshCount = 0;
  const onRefresh = () => { refreshCount += 1; };
  table.addEventListener("om:table:refresh", onRefresh);
  active.value = "";
  superuser.value = "";
  q.value = "oldman-json-table-no-match";
  form.requestSubmit();
  q.value = finalQuery;
  form.requestSubmit();
  for (let index = 0; index < 80; index += 1) {
    const hasFinalRow = Array.from(table.querySelectorAll("[data-om-table-row]")).some((row) => row.textContent.includes(finalQuery));
    if (refreshCount > 0 && table.dataset.omStatus === "success" && hasFinalRow) break;
    await sleep(100);
  }
  table.removeEventListener("om:table:refresh", onRefresh);
  if (refreshCount === 0) failures.push("users rapid search did not refresh");
  if (!Array.from(table.querySelectorAll("[data-om-table-row]")).some((row) => row.textContent.includes(finalQuery))) {
    failures.push("users rapid search did not keep the latest result");
  }
  return { failures };
})()
""",
        timeout=12.0,
    )
    rapid_search_failures = assertion_failures(rapid_search_result)
    result.pageErrors.extend(f"users rapid search: {failure}" for failure in rapid_search_failures)
    if rapid_search_failures:
        return

    before = install_request_probe(client)
    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#users-table']");
  if (!form) return { failures: ["missing users filter form"] };
  const q = form.querySelector("[name='q']");
  const active = form.querySelector("[name='is_active']");
  const superuser = form.querySelector("[name='is_superuser']");
  q.value = "";
  active.value = "true";
  superuser.value = "false";
  active.dispatchEvent(new Event("change", { bubbles: true }));
  superuser.dispatchEvent(new Event("change", { bubbles: true }));
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"users filters: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        wait_for_table_request(
            client,
            "/users/table",
            {"filter.is_active": "true", "filter.is_superuser": "false"},
            before,
            "users filters",
            result,
        )

    password_result = perform_user_password_gate(client, before)
    password_result_failures = assertion_failures(password_result)
    result.pageErrors.extend(f"users password: {failure}" for failure in password_result_failures)
    if password_result_failures:
        result.pageErrors.append(f"users password state: {password_result.get('modalState')}")
    else:
        modal_path = required_success_route(
            password_result,
            "modalPath",
            r"/users/[0-9]+/password-modal",
            "users password",
        )
        action_path = required_success_route(
            password_result,
            "actionPath",
            r"/users/[0-9]+/password",
            "users password",
        )
        if modal_path.removesuffix("-modal") != action_path:
            raise VerificationError("users password success payload routes use different users")
        wait_for_endpoint_request(client, modal_path, {}, before, "users password modal", result, noun="users password modal 请求")
        wait_for_endpoint_request(client, action_path, {}, before, "user password valid submit", result, noun="user password valid submit 请求")
        wait_for_table_request(client, "/users/table", {}, before, "user password valid submit table reload", result)
        assert_user_password_login_result(base_url, result)

    create_edit_delete_result = perform_user_create_edit_delete_gate(client, base_url, result)
    create_edit_delete_result_failures = assertion_failures(create_edit_delete_result)
    result.pageErrors.extend(f"users create/edit/delete: {failure}" for failure in create_edit_delete_result_failures)

    before = install_request_probe(client)
    status_result = perform_user_status_protection_gate(client, before)
    status_result_failures = assertion_failures(status_result)
    result.pageErrors.extend(f"users status: {failure}" for failure in status_result_failures)
    if not status_result_failures:
        action_path = required_success_route(
            status_result,
            "actionPath",
            r"/users/[0-9]+/status",
            "users status",
        )
        wait_for_endpoint_request(client, action_path, {}, before, "cannot disable current user", result, noun="cannot disable current user 请求")

    delete_result = perform_user_delete_protection_gate(client, before)
    delete_result_failures = assertion_failures(delete_result)
    result.pageErrors.extend(f"users delete: {failure}" for failure in delete_result_failures)
    if not delete_result_failures:
        action_path = required_success_route(
            delete_result,
            "actionPath",
            r"/users/[0-9]+/delete",
            "users delete",
        )
        wait_for_endpoint_request(client, action_path, {}, before, "cannot delete superuser", result, noun="cannot delete superuser 请求")


def assert_user_session_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证当前会话页的顶栏菜单、普通 modal、远程密码 Form 和反馈。"""
    component_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  if (!document.body.textContent.includes("User Session")) failures.push("missing user session heading");
  if (document.body.textContent.includes("User Management CRUD")) failures.push("user session page leaked CRUD wording");
  if (!document.querySelector("#user-session-feedback[data-om-component='feedback']")) failures.push("missing user session feedback");
  if (!document.querySelector("form[data-om-component='form'][data-user-session-readonly-form]")) failures.push("missing user session readonly form");
  if (!document.querySelector("#user-session-permissions-modal[data-om-component='modal']")) failures.push("missing user session permissions modal");
  if (!document.querySelector("#user-session-password-modal[data-om-component='modal']")) failures.push("missing user session password modal");
  if (!document.querySelector("[data-om-modal-url='/user-session/password-modal'][data-om-modal-target='#user-session-password-modal']")) {
    failures.push("missing user session password remote trigger");
  }
  if (!document.querySelector('a[href="/logout"]')) failures.push("missing user session logout link");
  return { failures };
})()
""",
        timeout=5.0,
    )
    component_result_failures = assertion_failures(component_result)
    result.pageErrors.extend(f"user session components: {failure}" for failure in component_result_failures)
    if component_result_failures:
        return

    before = install_request_probe(client)
    clear_request_probe(client)
    valid_password = os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD)
    interaction_result = client.evaluate(
        rf"""
(async () => {{
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {{
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  }};

  const userToggle = document.querySelector("#page-header-user-dropdown");
  userToggle?.click();
  await sleep(150);
  const userMenu = userToggle?.closest("[data-om-component='dropdown'], .oldman-dropdown, .dropdown")?.querySelector("[data-om-dropdown-menu], .dropdown-menu");
  if (!userMenu || userMenu.hidden || (!userMenu.classList.contains("show") && userMenu.classList.contains("hidden"))) failures.push("user session topbar dropdown did not open");
  if (!userMenu?.querySelector('a[href="/user-session"]')) failures.push("user session topbar dropdown missing session link");
  document.body.click();
  await sleep(100);

  document.querySelector("[data-om-modal-target='#user-session-permissions-modal']")?.click();
  for (let index = 0; index < 40 && !document.querySelector("#user-session-permissions-modal.is-open, #user-session-permissions-modal.show"); index += 1) {{
    await sleep(100);
  }}
  if (!visible(document.querySelector("#user-session-permissions-modal.is-open, #user-session-permissions-modal.show"))) failures.push("user session permissions modal did not open");
  document.querySelector("#user-session-permissions-modal.is-open [data-om-modal-close], #user-session-permissions-modal.show [data-bs-dismiss='modal']")?.click();
  for (let index = 0; index < 40 && document.querySelector("#user-session-permissions-modal.is-open, #user-session-permissions-modal.show"); index += 1) {{
    await sleep(100);
  }}
  if (document.querySelector("#user-session-permissions-modal.is-open, #user-session-permissions-modal.show")) failures.push("user session permissions modal did not close");

  const trigger = document.querySelector("[data-om-modal-target='#user-session-password-modal'][data-om-modal-url='/user-session/password-modal']");
  if (!trigger) return {{ failures: [...failures, "missing user session password trigger"] }};
  const modalPath = new URL(trigger.getAttribute("data-om-modal-url") || "", location.href).pathname;
  trigger.click();
  for (let index = 0; index < 60 && !document.querySelector("#user-session-password-modal.is-open form[data-om-form], #user-session-password-modal.show form[data-om-form]"); index += 1) {{
    await sleep(100);
  }}
  const modal = document.querySelector("#user-session-password-modal.is-open, #user-session-password-modal.show");
  const form = modal?.querySelector("form[data-om-form]");
  if (!visible(modal)) failures.push("user session password modal is not visible");
  if (form?.getAttribute("data-om-component") !== "form") failures.push("user session password form is not form");
  if (!form?.querySelector("[data-om-component='form-validator']")) failures.push("user session password form validator is missing");
  const password = form?.querySelector("[name='password']");
  const confirm = form?.querySelector("[name='confirm_password']");
  if (!password || !confirm) failures.push("user session password form missing fields");
  if (form && new URL(form.getAttribute("action") || "", location.href).pathname !== "/user-session/password") {{
    failures.push("user session password form action is not session endpoint");
  }}
  const visibleFooters = Array.from(modal?.querySelectorAll(".om-modal-footer, .modal-footer") || []).filter(visible);
  if (visibleFooters.length !== 1) {{
    failures.push("user session password modal has duplicate visible footers");
  }}
  if (password?.getAttribute("minlength") !== "8") failures.push("user session password minlength missing");
  if (!password?.getAttribute("pattern")) failures.push("user session password pattern missing");
  if (failures.length) return {{ failures, modalPath }};

  const actionPath = new URL(form.getAttribute("action") || "", location.href).pathname;
  const postCount = () => (window.__oldmanRequestLog || []).filter((request) => {{
    try {{
      return new URL(request.url, location.href).pathname === actionPath && String(request.method || "").toUpperCase() === "POST";
    }} catch {{
      return false;
    }}
  }}).length;

  const weakBefore = postCount();
  password.value = "abc";
  confirm.value = "abc";
  password.dispatchEvent(new Event("input", {{ bubbles: true }}));
  confirm.dispatchEvent(new Event("input", {{ bubbles: true }}));
  form.requestSubmit();
  await sleep(350);
  if (!form.classList.contains("was-validated")) failures.push("user session weak password did not mark form validated");
  if (postCount() > weakBefore) failures.push("user session weak password submitted ajax request");
  if (!visible(modal)) failures.push("user session password modal closed after weak password");
  if (failures.length) return {{ failures, modalPath, actionPath }};

  password.value = {json.dumps(valid_password)};
  confirm.value = "MismatchPass!2026";
  password.dispatchEvent(new Event("input", {{ bubbles: true }}));
  confirm.dispatchEvent(new Event("input", {{ bubbles: true }}));
  form.requestSubmit();
  for (let index = 0; index < 60 && !form.querySelector("[data-om-error-for='confirm_password']:not([hidden])"); index += 1) {{
    await sleep(100);
  }}
  const confirmError = form.querySelector("[data-om-error-for='confirm_password']:not([hidden])");
  if (!confirmError?.textContent.includes("Passwords do not match")) failures.push("user session password mismatch did not show field error");
  if (!visible(modal)) failures.push("user session password modal closed after mismatch");
  if (failures.length) return {{ failures, modalPath, actionPath }};

  password.value = {json.dumps(valid_password)};
  confirm.value = {json.dumps(valid_password)};
  password.dispatchEvent(new Event("input", {{ bubbles: true }}));
  confirm.dispatchEvent(new Event("input", {{ bubbles: true }}));
  form.requestSubmit();
  for (let index = 0; index < 70 && document.querySelector("#user-session-password-modal.is-open, #user-session-password-modal.show"); index += 1) {{
    await sleep(100);
  }}
  if (document.querySelector("#user-session-password-modal.is-open, #user-session-password-modal.show")) failures.push("user session password valid submit did not close modal");
  for (let index = 0; index < 50 && !Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("Session password changed")); index += 1) {{
    await sleep(100);
  }}
  if (!Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("Session password changed"))) {{
    failures.push("user session success toast missing");
  }}
  return {{ failures, modalPath, actionPath }};
}})()
""",
        timeout=28.0,
    )
    interaction_result_failures = assertion_failures(interaction_result)
    result.pageErrors.extend(f"user session: {failure}" for failure in interaction_result_failures)
    if not interaction_result_failures:
        modal_path = required_success_string(interaction_result, "modalPath", "user session password")
        if modal_path != "/user-session/password-modal":
            raise VerificationError("user session password success payload modalPath is not /user-session/password-modal")
        action_path = required_success_string(interaction_result, "actionPath", "user session password")
        if action_path != "/user-session/password":
            raise VerificationError("user session password success payload actionPath is not /user-session/password")
        wait_for_endpoint_request(client, modal_path, {}, before, "user session password modal", result, noun="user session password modal 请求")
        wait_for_endpoint_request(client, action_path, {}, before, "user session password valid submit", result, noun="user session password valid submit 请求")


def assert_notifications_center_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证通知中心筛选、详情弹窗、批量清除和无选择反馈。"""
    stat_card_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const cards = Array.from(document.querySelectorAll("[data-om-component='notifications-center'] .om-card, [data-om-component='notifications-center'] .row .card")).slice(0, 4);
  if (cards.length !== 4) failures.push(`notification stat card count is ${cards.length}`);
  for (const card of cards) {
    const label = card.querySelector("p")?.textContent?.trim() || "unknown";
    const avatar = card.querySelector(".om-avatar-sm, .avatar-sm");
    const iconBox = card.querySelector(".om-avatar-title, .avatar-title");
    const value = card.querySelector("h4");
    if (!avatar) {
      failures.push(`notification stat card ${label} missing avatar wrapper`);
      continue;
    }
    if (!iconBox) {
      failures.push(`notification stat card ${label} missing avatar title`);
      continue;
    }
    const avatarRect = avatar.getBoundingClientRect();
    const iconRect = iconBox.getBoundingClientRect();
    const valueRect = value?.getBoundingClientRect();
    if (avatarRect.width < 40 || avatarRect.width > 56 || avatarRect.height < 40 || avatarRect.height > 56) {
      failures.push(`notification stat card ${label} avatar size is ${Math.round(avatarRect.width)}x${Math.round(avatarRect.height)}`);
    }
    if (iconRect.width < 40 || iconRect.width > 56 || iconRect.height < 40 || iconRect.height > 56) {
      failures.push(`notification stat card ${label} avatar-title expanded to ${Math.round(iconRect.width)}x${Math.round(iconRect.height)}`);
    }
    if (valueRect && iconRect.left < valueRect.right + 8) {
      failures.push(`notification stat card ${label} icon overlaps value`);
    }
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    stat_card_result_failures = assertion_failures(stat_card_result)
    result.pageErrors.extend(f"notifications stat cards: {failure}" for failure in stat_card_result_failures)

    topbar_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const items = Array.from(document.querySelectorAll("#notificationDropdown .notification-item"));
  if (!items.length) failures.push("notifications topbar has no real notification items");
  if (!items.some((item) => item.textContent.includes("Browser Gate Notification"))) {
    failures.push("notifications topbar missing browser gate notification");
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    topbar_result_failures = assertion_failures(topbar_result)
    result.pageErrors.extend(f"notifications topbar: {failure}" for failure in topbar_result_failures)

    before = install_request_probe(client)
    clear_request_probe(client)
    filter_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#notifications-table']");
  const table = document.querySelector("#notifications-table[data-om-component='table']");
  if (!form) failures.push("missing notifications filter form");
  if (!table) failures.push("missing notifications table");
  const q = form?.querySelector("[name='q']");
  const type = form?.querySelector("[name='notification_type']");
  const severity = form?.querySelector("[name='severity']");
  if (!q) failures.push("missing notifications q filter");
  if (!type) failures.push("missing notifications type filter");
  if (!severity) failures.push("missing notifications severity filter");
  if (failures.length) return { failures };
  q.value = "Browser Gate Notification";
  type.value = "decision";
  severity.value = "critical";
  q.dispatchEvent(new Event("input", { bubbles: true }));
  type.dispatchEvent(new Event("change", { bubbles: true }));
  severity.dispatchEvent(new Event("change", { bubbles: true }));
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    filter_result_failures = assertion_failures(filter_result)
    result.pageErrors.extend(f"notifications filters: {failure}" for failure in filter_result_failures)
    if not filter_result_failures:
        wait_for_table_request(
            client,
            "/notifications/table",
            {"q": "Browser Gate Notification", "filter.notification_type": "decision", "filter.severity": "critical"},
            before,
            "notifications filters",
            result,
        )
        wait_for_table_success(client, "/notifications/table", "notifications filters", result)

    clear_transient_browser_overlays(client)
    detail_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let index = 0; index < 80 && !Array.from(document.querySelectorAll("#notifications-table [data-om-table-row]")).some((row) => row.textContent.includes("Browser Gate Notification")); index += 1) {
    await sleep(100);
  }
  const row = Array.from(document.querySelectorAll("#notifications-table [data-om-table-row]")).find((item) => item.textContent.includes("Browser Gate Notification"));
  if (!row) return { failures: ["missing browser gate notification row"] };
  row.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")?.click();
  await sleep(120);
  const action = row.querySelector("[data-om-modal-target='#notification-detail-modal'][data-om-modal-url]");
  if (!action) return { failures: ["missing notification detail action"] };
  const detailPath = new URL(action.getAttribute("data-om-modal-url") || "", location.href).pathname;
  action.click();
  for (let index = 0; index < 50 && !document.querySelector("#notification-detail-modal.is-open, #notification-detail-modal.show"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#notification-detail-modal.is-open, #notification-detail-modal.show");
  if (!modal) failures.push("notification detail modal did not open");
  if (!modal?.textContent.includes("Browser Gate Notification")) failures.push("notification detail modal missing seeded content");
  return { failures, detailPath };
})()
""",
        timeout=12.0,
    )
    detail_result_failures = assertion_failures(detail_result)
    result.pageErrors.extend(f"notifications detail: {failure}" for failure in detail_result_failures)
    if not detail_result_failures:
        detail_path = required_success_route(
            detail_result,
            "detailPath",
            r"/notifications/detail/[^/]+",
            "notifications detail",
        )
        assert_visible_modal_fits(client, result, "notifications detail modal", "#notification-detail-modal.is-open, #notification-detail-modal.show")
        wait_for_endpoint_request(client, detail_path, {}, before, "notification detail modal", result, noun="notification detail modal 请求")
    client.evaluate("document.querySelector(\"#notification-detail-modal.is-open [data-om-modal-close], #notification-detail-modal.show [data-bs-dismiss='modal']\")?.click()", timeout=5.0)
    client.pump(0.2)

    clear_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
  const clearButton = document.querySelector("[data-notifications-clear-selected]");
  const rows = Array.from(document.querySelectorAll("#notifications-table [data-om-table-row]")).filter((item) => item.textContent.includes("Browser Gate Notification"));
  if (!clearButton) failures.push("missing data-notifications-clear-selected button");
  if (!rows.length) failures.push("missing row before clear");
  const checkboxes = rows.map((row) => row.querySelector("[data-om-table-select-row]")).filter(Boolean);
  if (checkboxes.length !== rows.length) failures.push("missing notification row checkbox");
  if (failures.length) return { failures };
  for (const checkbox of checkboxes) {
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event("change", { bubbles: true }));
  }
  clearButton.click();
  for (let index = 0; index < 40 && !Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("Notifications cleared")); index += 1) {
    await sleep(100);
  }
  if (!Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("Notifications cleared"))) failures.push("notification center selected clear toast missing");
  if (Array.from(document.querySelectorAll("#notifications-table [data-om-table-row]")).some((item) => item.textContent.includes("Browser Gate Notification"))) failures.push("notification row still visible after clear");
  const empty = document.querySelector("[data-notifications-empty]");
  if (!empty || empty.hidden) failures.push("notification center empty state missing after clear");

  document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
  clearButton.click();
  for (let index = 0; index < 40 && !Array.from(document.querySelectorAll(".swal2-popup")).some((popup) => popup.textContent.includes("No notifications selected")); index += 1) {
    await sleep(100);
  }
  if (!Array.from(document.querySelectorAll(".swal2-popup")).some((popup) => popup.textContent.includes("No notifications selected"))) failures.push("notification center no selection feedback missing");
  return { failures };
})()
""",
        timeout=12.0,
    )
    clear_result_failures = assertion_failures(clear_result)
    result.pageErrors.extend(f"notifications clear: {failure}" for failure in clear_result_failures)


def assert_back_to_top_interaction(client: CDPClient, result: VerificationResult) -> None:
    """验证 Dashboard 壳层的回到顶部按钮。"""
    back_to_top_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const button = document.querySelector("#back-to-top");
  if (!button) return { failures: ["back-to-top button missing"] };
  document.activeElement?.blur?.();
  await sleep(250);
  window.scrollTo(0, 240);
  await sleep(100);
  if ((window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0) < 100) {
    if (document.scrollingElement) document.scrollingElement.scrollTop = 240;
    document.documentElement.scrollTop = 240;
    document.body.scrollTop = 240;
    await sleep(100);
  }
  window.dispatchEvent(new Event("scroll"));
  await sleep(100);
  const currentScrollTop = window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
  if (currentScrollTop < 100) failures.push("dashboard did not scroll far enough for back-to-top check");
  const visible = !button.hidden && getComputedStyle(button).display !== "none" && button.getAttribute("aria-hidden") !== "true";
  if (currentScrollTop >= 100 && !visible) failures.push("back-to-top button did not become visible");
  button.click();
  for (let index = 0; index < 20 && (window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0) > 2; index += 1) {
    await sleep(50);
  }
  const remainingTop = window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
  if (remainingTop > 2) failures.push("back-to-top button did not scroll to top");
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(str(failure) for failure in assertion_failures(back_to_top_result))


def perform_user_password_gate(client: CDPClient, before: dict[str, Any]) -> dict[str, Any]:
    """执行普通用户密码 modal 的错误和成功提交。"""
    clear_request_probe(client)
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const waitForUsersRow = async (query) => {
    const form = document.querySelector("form[data-om-table-target='#users-table']");
    const table = document.querySelector("#users-table");
    if (!form || !table) return false;
    form.querySelector("[name='q']").value = query;
    form.querySelector("[name='is_active']").value = "";
    form.querySelector("[name='is_superuser']").value = "";
    form.requestSubmit();
    for (let index = 0; index < 80; index += 1) {
      const hasRow = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((row) => row.textContent.includes(query));
      if (table.dataset.omStatus === "success" && hasRow) return true;
      await sleep(100);
    }
    return false;
  };
  const submitFilter = async (query) => {
    return waitForUsersRow(query);
  };
  if (!await submitFilter("browser_gate_user")) return { failures: ["browser_gate_user filter did not settle"] };
  const row = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).find((item) => item.textContent.includes("browser_gate_user"));
  if (!row) return { failures: ["missing browser_gate_user row"] };
  row.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")?.click();
  await sleep(120);
  const action = row.querySelector("[data-om-modal-target='#user-password-modal'][data-om-modal-url]");
  if (!action) return { failures: ["missing user password action"] };
  const modalPath = new URL(action.getAttribute("data-om-modal-url") || "", location.href).pathname;
  action.click();
  for (let index = 0; index < 50 && !document.querySelector("#user-password-modal.is-open form[data-om-form], #user-password-modal.show form[data-om-form]"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#user-password-modal.is-open, #user-password-modal.show");
  const form = modal?.querySelector("form[data-om-form]");
  if (!visible(modal)) failures.push("user password modal is not visible");
  if (form?.getAttribute("data-om-component") !== "form") failures.push("user password form is not form");
  if (!form?.querySelector("[data-om-component='form-validator']")) failures.push("user password form validator is missing");
  const password = form?.querySelector("[name='password']");
  const confirm = form?.querySelector("[name='confirm_password']");
  if (!password || !confirm) failures.push("user password form missing fields");
  const visibleFooters = Array.from(modal?.querySelectorAll(".om-modal-footer, .modal-footer") || []).filter(visible);
  if (visibleFooters.length !== 1) failures.push("user password modal has duplicate visible footers");
  if (password?.getAttribute("minlength") !== "8") failures.push("user password minlength missing");
  if (!password?.getAttribute("pattern")) failures.push("user password pattern missing");
  if (failures.length) return { failures, modalPath };

  const actionPath = new URL(form.getAttribute("action") || "", location.href).pathname;
  const weakRequestCount = () => (window.__oldmanRequestLog || []).filter((request) => {
    try {
      return new URL(request.url, location.href).pathname === actionPath && String(request.method || "").toUpperCase() === "POST";
    } catch {
      return false;
    }
  }).length;
  const weakBefore = weakRequestCount();
  password.value = "abc";
  confirm.value = "abc";
  password.dispatchEvent(new Event("input", { bubbles: true }));
  confirm.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  await sleep(350);
  if (!form.classList.contains("was-validated")) failures.push("user weak password did not mark form validated");
  if (weakRequestCount() > weakBefore) failures.push("user weak password submitted ajax request");
  if (!visible(modal)) failures.push("user password modal closed after weak password");
  if (failures.length) return { failures, modalPath, actionPath };

  password.value = "UserGatePass!2026";
  confirm.value = "MismatchPass!2026";
  form.requestSubmit();
  for (let index = 0; index < 50 && !form.querySelector("[data-om-error-for='confirm_password']:not([hidden])"); index += 1) {
    await sleep(100);
  }
  const confirmError = form.querySelector("[data-om-error-for='confirm_password']:not([hidden])");
  if (!confirmError?.textContent.includes("Passwords do not match")) failures.push("user password mismatch did not show field error");
  if (!visible(modal)) failures.push("user password modal closed after mismatch");
  if (failures.length) return { failures, modalPath, actionPath };

  password.value = "UserGatePass!2026";
  confirm.value = "UserGatePass!2026";
  password.dispatchEvent(new Event("input", { bubbles: true }));
  confirm.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  for (let index = 0; index < 60 && document.querySelector("#user-password-modal.is-open, #user-password-modal.show"); index += 1) {
    await sleep(100);
  }
  if (document.querySelector("#user-password-modal.is-open, #user-password-modal.show")) failures.push("user password valid submit did not close modal");
  for (let index = 0; index < 50 && !document.querySelector(".toastify.om-toast.on"); index += 1) {
    await sleep(100);
  }
  const toast = document.querySelector(".toastify.om-toast.on");
  if (!toast?.textContent.includes("Password changed")) failures.push("users success toast did not show password change");
  for (let index = 0; index < 50 && !Array.from(document.querySelectorAll("#notificationDropdown .notification-item")).some((item) => item.textContent.includes("Password changed")); index += 1) {
    await sleep(100);
  }
  if (!Array.from(document.querySelectorAll("#notificationDropdown .notification-item")).some((item) => item.textContent.includes("Password changed"))) {
    failures.push("users password change did not add topbar notification");
  }
  return { failures, modalPath, actionPath, modalState: { state: modal?.dataset.omState || "", status: modal?.dataset.omStatus || "", className: modal?.className || "", hidden: modal?.hidden ?? null, html: modal?.querySelector(".om-modal-body, .modal-body")?.innerHTML || "" } };
})()
""",
        timeout=20.0,
    )


def perform_user_status_protection_gate(client: CDPClient, before: dict[str, Any]) -> dict[str, Any]:
    """执行当前用户禁用保护门禁。"""
    clear_request_probe(client)
    return client.evaluate(
        rf"""
(async () => {{
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
  const username = {json.dumps(DEFAULT_USERNAME)};
  const form = document.querySelector("form[data-om-table-target='#users-table']");
  const table = document.querySelector("#users-table");
  form.querySelector("[name='q']").value = username;
  form.querySelector("[name='is_active']").value = "";
  form.querySelector("[name='is_superuser']").value = "";
  form.requestSubmit();
  for (let index = 0; index < 80 && !(table?.dataset.omStatus === "success" && Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((row) => row.textContent.includes(username))); index += 1) {{
    await sleep(100);
  }}
  const row = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).find((item) => item.textContent.includes(username));
  if (!row) return {{ failures: ["missing current user row"] }};
  row.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")?.click();
  await sleep(120);
  const action = row.querySelector("[data-om-modal-target='#user-status-modal'][data-om-modal-url]");
  if (!action) return {{ failures: ["missing user status action"] }};
  action.click();
  for (let index = 0; index < 50 && !document.querySelector("#user-status-modal.is-open form[data-om-form], #user-status-modal.show form[data-om-form]"); index += 1) {{
    await sleep(100);
  }}
  const modal = document.querySelector("#user-status-modal.is-open, #user-status-modal.show");
  const submitForm = modal?.querySelector("form[data-om-form]");
  if (!submitForm) return {{ failures: ["user status modal did not load form"] }};
  const actionPath = new URL(submitForm.getAttribute("action") || "", location.href).pathname;
  submitForm.requestSubmit();
  const message = submitForm.querySelector("[data-om-form-message]");
  for (let index = 0; index < 50 && !(message?.textContent.includes("cannot disable current user") && !message.hidden); index += 1) {{
    await sleep(100);
  }}
  if (!message?.textContent.includes("cannot disable current user") || message.hidden) failures.push("cannot disable current user Form message missing");
  if (!document.querySelector("#user-status-modal.is-open, #user-status-modal.show")) failures.push("user status modal closed after protected submit");
  document.querySelector("#user-status-modal.is-open [data-om-modal-close], #user-status-modal.show [data-om-modal-close]")?.click();
  await sleep(120);
  return {{ failures, actionPath }};
}})()
""",
        timeout=18.0,
    )


def perform_user_delete_protection_gate(client: CDPClient, before: dict[str, Any]) -> dict[str, Any]:
    """执行超级用户删除保护门禁。"""
    clear_request_probe(client)
    return client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
  const username = "browser_gate_superuser";
  const form = document.querySelector("form[data-om-table-target='#users-table']");
  const table = document.querySelector("#users-table");
  form.querySelector("[name='q']").value = username;
  form.querySelector("[name='is_active']").value = "";
  form.querySelector("[name='is_superuser']").value = "";
  form.requestSubmit();
  for (let index = 0; index < 80 && !(table?.dataset.omStatus === "success" && Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((row) => row.textContent.includes(username))); index += 1) {
    await sleep(100);
  }
  const row = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).find((item) => item.textContent.includes(username));
  if (!row) return { failures: ["missing browser_gate_superuser row"] };
  row.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")?.click();
  await sleep(120);
  const action = row.querySelector("[data-om-modal-target='#user-delete-modal'][data-om-modal-url]");
  if (!action) return { failures: ["missing user delete action"] };
  action.click();
  for (let index = 0; index < 50 && !document.querySelector("#user-delete-modal.is-open form[data-om-form], #user-delete-modal.show form[data-om-form]"); index += 1) {{
    await sleep(100);
  }}
  const modal = document.querySelector("#user-delete-modal.is-open, #user-delete-modal.show");
  const submitForm = modal?.querySelector("form[data-om-form]");
  if (!submitForm) return { failures: ["user delete modal did not load form"] };
  const actionPath = new URL(submitForm.getAttribute("action") || "", location.href).pathname;
  submitForm.requestSubmit();
  const message = submitForm.querySelector("[data-om-form-message]");
  for (let index = 0; index < 50 && !(message?.textContent.includes("cannot delete superuser") && !message.hidden); index += 1) {
    await sleep(100);
  }
  if (!message?.textContent.includes("cannot delete superuser") || message.hidden) failures.push("cannot delete superuser Form message missing");
  if (!document.querySelector("#user-delete-modal.is-open, #user-delete-modal.show")) failures.push("user delete modal closed after protected submit");
  document.querySelector("#user-delete-modal.is-open [data-om-modal-close], #user-delete-modal.show [data-om-modal-close]")?.click();
  await sleep(120);
  return { failures, actionPath };
})()
""",
        timeout=18.0,
    )


def wait_for_path_pattern(client: CDPClient, pattern: str, label: str, result: VerificationResult, timeout: float = 8.0) -> bool:
    """等待浏览器地址 path 匹配指定正则。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw_state = client.evaluate(
                "(() => ({ path: location.pathname, ready: document.readyState }))()",
                timeout=2.0,
            )
        except VerificationError:
            time.sleep(0.1)
            continue
        state = required_payload_object(raw_state, f"{label} path pattern state")
        path = required_string_field(state, "path", f"{label} path pattern state")
        ready = required_string_field(state, "ready", f"{label} path pattern state")
        if ready == "complete" and re.match(pattern, path):
            return True
        time.sleep(0.1)
    result.pageErrors.append(f"{label}: path did not match {pattern}")
    return False


def assert_toast_text(client: CDPClient, text: str, label: str, result: VerificationResult, timeout: float = 4.0) -> bool:
    """等待 SweetAlert toast 出现指定文本。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.evaluate(
            rf"""
(() => {{
  return Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes({json.dumps(text)}));
}})()
""",
            timeout=2.0,
        )
        if state:
            return True
        time.sleep(0.1)
    result.pageErrors.append(f"{label}: toast missing {text}")
    return False


def wait_for_component_mounted(client: CDPClient, selector: str, label: str, result: VerificationResult, timeout: float = 8.0) -> bool:
    """等待声明式组件完成挂载，避免门禁早于前端初始化提交。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = required_payload_object(
            client.evaluate(
                rf"""
(() => {{
  const element = document.querySelector({json.dumps(selector)});
  return {{
    exists: Boolean(element),
    state: element?.getAttribute("data-om-component-state") || "",
    ready: document.readyState
  }};
}})()
""",
                timeout=2.0,
            ),
            f"{label} component mounted state",
        )
        exists = required_success_bool(state, "exists", f"{label} component mounted state")
        component_state = required_string_field(state, "state", f"{label} component mounted state")
        required_string_field(state, "ready", f"{label} component mounted state")
        if exists and component_state == "mounted":
            return True
        time.sleep(0.1)
    result.pageErrors.append(f"{label}: component did not mount for {selector}")
    return False


def perform_user_create_edit_delete_gate(client: CDPClient, base_url: str, result: VerificationResult) -> dict[str, Any]:
    """执行 Users 创建、编辑保存和普通用户删除成功路径门禁。"""
    clear_request_probe(client)
    navigate(client, urllib.parse.urljoin(base_url, "/users/new"))
    if not wait_for_component_mounted(client, "form[data-om-component='form'][data-om-form][action='/users/new']", "users create form", result):
        return {"failures": ["users create form component did not mount"]}
    create_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const username = `browser_gate_created_${Date.now()}`;
  const email = `${username}@example.test`;
  const fill = (form, name, value) => {
    const field = form.querySelector(`[name='${name}']`);
    if (!field) {
      failures.push(`missing ${name} field`);
      return;
    }
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else {
      field.value = value;
    }
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  };
  let form = document.querySelector("form[data-om-component='form'][data-om-form][action='/users/new']");
  if (!form) return { failures: ["missing users create form"] };
  if (!form.hasAttribute("data-om-form-validate")) failures.push("users create form missing validation flag");
  if (!form.querySelector("[data-om-component='form-validator']")) failures.push("users create form missing form-validator component");
  if (!document.querySelector("#users-form-feedback[data-om-component='feedback']")) failures.push("users create form missing feedback component");
  fill(form, "username", username);
  fill(form, "email", email);
  fill(form, "display_name", "Browser Gate Created User");
  fill(form, "password", "CreatedPass2026!");
  fill(form, "confirm_password", "CreatedPass2026!");
  fill(form, "is_active", true);
  fill(form, "is_superuser", false);
  if (failures.length) return { failures };
  const createPath = new URL(form.getAttribute("action") || "", location.href).pathname;
  form.requestSubmit();
  for (let index = 0; index < 16 && !Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User created")); index += 1) {
    await sleep(50);
  }
  const createToast = Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User created"));
  return { failures, createPath, username, createToast };
})()
""",
        timeout=6.0,
    )
    create_result_failures = assertion_failures(create_result)
    if create_result_failures:
        return create_result
    create_path = required_success_string(create_result, "createPath", "users create")
    if create_path != "/users/new":
        raise VerificationError("users create success payload createPath is not /users/new")
    created_username = required_success_string(create_result, "username", "users create")
    if required_success_bool(create_result, "createToast", "users create") is not True:
        raise VerificationError("users create success toast missing")
    time.sleep(1.2)
    if not wait_for_path_pattern(client, r"^/users/\d+/edit$", "users create redirect", result):
        return {**create_result, "failures": ["users create did not redirect to edit page"]}
    if not wait_for_component_mounted(client, "form[data-om-component='form'][data-om-form]", "users edit form", result):
        return {**create_result, "failures": ["users edit form component did not mount"]}

    edit_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const form = document.querySelector(`form[data-om-component='form'][data-om-form][action='${location.pathname}']`);
  if (!form) return { failures: ["missing users edit form"] };
  if (!form.hasAttribute("data-om-form-validate")) failures.push("users edit form missing validation flag");
  const displayName = form.querySelector("[name='display_name']");
  if (!displayName) failures.push("missing display_name field");
  if (failures.length) return { failures };
  displayName.value = "Browser Gate Edited User";
  displayName.dispatchEvent(new Event("input", { bubbles: true }));
  displayName.dispatchEvent(new Event("change", { bubbles: true }));
  const editPath = new URL(form.getAttribute("action") || "", location.href).pathname;
  form.requestSubmit();
  for (let index = 0; index < 16 && !Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User saved")); index += 1) {
    await sleep(50);
  }
  const editToast = Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User saved"));
  return { failures, editPath, editToast };
})()
""",
        timeout=6.0,
    )
    edit_result_failures = assertion_failures(edit_result)
    if edit_result_failures:
        return {**create_result, **edit_result, "failures": edit_result_failures}
    edit_path = required_success_route(
        edit_result,
        "editPath",
        r"/users/[1-9][0-9]*/edit",
        "users edit",
    )
    if required_success_bool(edit_result, "editToast", "users edit") is not True:
        raise VerificationError("users edit success toast missing")
    edited_user_id = edit_path.split("/")[2]
    time.sleep(1.2)

    navigate(client, urllib.parse.urljoin(base_url, "/users"))
    wait_for_table_ready(client, "/users/table", "users create/edit/delete list", result)
    delete_script = r"""
(async () => {{
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const username = __USERNAME__;
  const form = document.querySelector("form[data-om-table-target='#users-table']");
  const table = document.querySelector("#users-table");
  if (!form || !table) return {{ failures: ["missing users list filter/table after edit"] }};
  const q = form.querySelector("[name='q']");
  const active = form.querySelector("[name='is_active']");
  const superuser = form.querySelector("[name='is_superuser']");
  if (!q || !active || !superuser) return {{ failures: ["missing users list filter fields after edit"] }};
  q.value = username;
  active.value = "";
  superuser.value = "";
  form.requestSubmit();
  for (let index = 0; index < 100; index += 1) {{
    const exists = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((row) => row.textContent.includes(username));
    if (table.dataset.omStatus === "success" && exists) break;
    await sleep(100);
  }}
  const row = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).find((item) => item.textContent.includes(username));
  if (!row) return {{ failures: ["created user row not found after edit"] }};
  if (!row?.textContent.includes("Browser Gate Edited User")) failures.push("edited display name did not appear in users table");
  row?.querySelector("[data-om-dropdown-toggle], [data-bs-toggle='dropdown']")?.click();
  await sleep(120);
  const action = row?.querySelector("[data-om-modal-target='#user-delete-modal'][data-om-modal-url]");
  if (!action) return {{ failures: [...failures, "missing delete action for created user"] }};
  action.click();
  for (let index = 0; index < 50 && !document.querySelector("#user-delete-modal.is-open form[data-om-form], #user-delete-modal.show form[data-om-form]"); index += 1) {
    await sleep(100);
  }
  const modal = document.querySelector("#user-delete-modal.is-open, #user-delete-modal.show");
  const deleteForm = modal?.querySelector("form[data-om-form]");
  if (!deleteForm) return {{ failures: [...failures, "created user delete modal did not load"] }};
  const deletePath = new URL(deleteForm.getAttribute("action") || "", location.href).pathname;
  deleteForm.requestSubmit();
  for (let index = 0; index < 60 && document.querySelector("#user-delete-modal.is-open, #user-delete-modal.show"); index += 1) {{
    await sleep(100);
  }}
  if (document.querySelector("#user-delete-modal.is-open, #user-delete-modal.show")) failures.push("created user delete did not close modal");
  for (let index = 0; index < 60 && !Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User deleted")); index += 1) {{
    await sleep(100);
  }}
  if (!Array.from(document.querySelectorAll(".toastify.om-toast.on")).some((toast) => toast.textContent.includes("User deleted"))) failures.push("created user delete success toast missing");
  q.value = username;
  form.requestSubmit();
  for (let index = 0; index < 100; index += 1) {{
    const exists = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((item) => item.textContent.includes(username));
    if (table.dataset.omStatus === "success" && !exists) break;
    await sleep(100);
  }}
  const stillExists = Array.from(document.querySelectorAll("#users-table [data-om-table-row]")).some((item) => item.textContent.includes(username));
  if (stillExists) failures.push("created user still visible after delete");
  return {{ failures, deletePath }};
}})()
""".replace("{{", "{").replace("}}", "}").replace("__USERNAME__", json.dumps(created_username))
    delete_result = client.evaluate(
        delete_script,
        timeout=25.0,
    )
    delete_result_failures = assertion_failures(delete_result)
    if not delete_result_failures:
        delete_path = required_success_route(
            delete_result,
            "deletePath",
            r"/users/[1-9][0-9]*/delete",
            "users delete",
        )
        if delete_path.split("/")[2] != edited_user_id:
            raise VerificationError("users edit/delete success payload routes use different users")
    failures = [*create_result_failures, *edit_result_failures, *delete_result_failures]
    return {**create_result, **edit_result, **delete_result, "failures": failures}


def assert_user_password_login_result(base_url: str, result: VerificationResult) -> None:
    """通过独立 HTTP cookie jar 验证用户改密后旧密码失效、新密码可登录。"""
    old_login = submit_login_form(base_url, "browser_gate_user", "UserGateOldPass!2026")
    if old_login.get("ok"):
        result.pageErrors.append("users password old password still logs in")
    new_login = submit_login_form(base_url, "browser_gate_user", "UserGatePass!2026")
    if not new_login.get("ok"):
        result.pageErrors.append(f"users password new password login failed: {new_login}")


def submit_login_form(base_url: str, username: str, password: str) -> dict[str, Any]:
    """使用独立 cookie jar 提交登录表单，避免污染当前浏览器会话。"""
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    login_url = urllib.parse.urljoin(base_url, "/login")
    try:
        login_html = opener.open(login_url, timeout=10).read().decode("utf-8", errors="replace")
        csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', login_html)
        if not csrf_match:
            return {"ok": False, "error": "missing csrf"}
        body = urllib.parse.urlencode(
            {
                "csrfmiddlewaretoken": csrf_match.group(1),
                "username": username,
                "password": password,
                "next": "/",
            }
        ).encode("utf-8")
        response = opener.open(
            urllib.request.Request(login_url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}),
            timeout=10,
        )
        final_path = urllib.parse.urlparse(response.geturl()).path
        final_query = urllib.parse.urlparse(response.geturl()).query
        return {"ok": final_path != "/login" and "invalid_credentials" not in final_query, "url": response.geturl()}
    except Exception as exc:  # noqa: BLE001 - 门禁需要把 HTTP 失败转为结构化错误。
        return {"ok": False, "error": str(exc)}


def assert_catalog_feed_upload_preview(client: CDPClient, label: str, result: VerificationResult) -> None:
    """验证 CatalogFeed 表单 preview-only upload 的选择、预览、删除和错误提示。"""
    upload_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const root = document.querySelector("[data-om-component='upload']");
  const input = root?.querySelector("[data-om-upload-input]");
  const preview = root?.querySelector("[data-om-upload-preview]");
  if (!root) failures.push("missing upload root");
  if (!input) failures.push("missing upload input");
  if (input?.getAttribute("name")) failures.push("preview-only upload input should not have name");
  if (!preview) failures.push("missing upload preview");
  if (!document.body.textContent.includes("does not update CatalogLogoAsset")) failures.push("missing preview-only CatalogLogoAsset warning");
  if (root?.getAttribute("data-om-component-state") !== "mounted") failures.push("upload component is not mounted");
  if (failures.length) return { failures };

  const setFiles = (files) => {
    const dataTransfer = new DataTransfer();
    for (const file of files) dataTransfer.items.add(file);
    Object.defineProperty(input, "files", { value: dataTransfer.files, configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitForImage = async (image) => {
    if (!image) return false;
    if (image.complete && image.naturalWidth > 0) return true;
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 1000);
      image.addEventListener("load", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
      image.addEventListener("error", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
    });
    await sleep(50);
    return image.complete && image.naturalWidth > 0;
  };

  const tinySvg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="#2563eb"/></svg>';
  setFiles([new File([tinySvg], "catalog-feed-logo.svg", { type: "image/svg+xml" })]);
  if (!preview.textContent.includes("catalog-feed-logo.svg")) failures.push("upload preview did not render selected file");
  const thumbnail = preview.querySelector("[data-om-upload-thumbnail][src]");
  if (!thumbnail) failures.push("upload image thumbnail did not render src");
  if (thumbnail && !(await waitForImage(thumbnail))) failures.push("upload image thumbnail did not finish loading");
  const remove = preview.querySelector("[data-om-upload-remove]");
  if (!remove) {
    failures.push("upload preview missing remove button");
  } else {
    remove.click();
    if (preview.textContent.includes("catalog-feed-logo.svg")) failures.push("upload remove did not clear selected file");
  }

  const bigPayload = new Uint8Array(1048577);
  setFiles([new File([bigPayload], "too-large-logo.txt", { type: "text/plain" })]);
  if (!preview.textContent.includes("too-large-logo.txt")) failures.push("oversize upload preview did not render file");
  if (!preview.textContent.includes("File is too large")) failures.push("oversize upload did not show error message");
  return { failures };
})()
""",
        timeout=8.0,
    )
    upload_result_failures = assertion_failures(upload_result)
    result.pageErrors.extend(f"{label} upload: {failure}" for failure in upload_result_failures)


def assert_catalog_feed_remote_channel_select(client: CDPClient, label: str, result: VerificationResult) -> None:
    """验证 CatalogFeed 编辑页 CatalogChannel 远程 select 有回显并可搜索。"""
    assertion_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const select = document.querySelector("select[name='catalog_channel_id']");
  const selected = select?.selectedOptions?.[0];
  if (!select) failures.push("missing catalog_channel_id select");
  if (select?.getAttribute("data-om-component") !== "select") failures.push("catalog_channel_id is not Oldman select component");
  if (!selected || !selected.value) failures.push("catalog_channel_id select has no selected value");
  if (!selected || !(selected.textContent || "").trim()) failures.push("catalog_channel_id select has no selected label");
  if (!select?.getAttribute("data-om-select-src")?.includes("/admin/select/catalog_channels")) failures.push("catalog_channel_id select src is not catalog_channels provider");
  return { failures };
})()
""",
        timeout=5.0,
    )
    assertion_result_failures = assertion_failures(assertion_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_result_failures)
    assert_remote_select_search(client, "/admin/select/catalog_channels", "select[name='catalog_channel_id']", "Browser", label, result)
    assert_remote_select_pagination(client, "/admin/select/catalog_channels", "select[name='catalog_channel_id']", label, result)


def create_catalog_feed_gate_record(client: CDPClient, base_url: str, catalog_channel_id: str, result: VerificationResult) -> tuple[str, str]:
    """通过真实表单创建临时 CatalogFeed，返回编辑路径和 TVG ID。"""
    suffix = str(int(time.time() * 1000))
    tvg_id = f"browser.gate.feed.{suffix}"
    canonical_name = f"Browser Gate Feed {suffix}"
    navigate(client, urllib.parse.urljoin(base_url, "/catalog-feeds/new"))
    submit_business_form(
        client,
        "/catalog-feeds/new",
        {
            "catalog_channel_id": catalog_channel_id,
            "feed_suffix": f"gate-{suffix}",
            "tvg_id": tvg_id,
            "is_default": True,
            "canonical_name": canonical_name,
            "accepted_names_text": canonical_name,
            "compatible_tvg_ids_text": tvg_id,
            "service_country_code": "US",
            "region_code": "US-CA",
            "language_hints_text": "en",
            "timezone_hints_text": "America/Los_Angeles",
            "version_kind": "default",
            "status": "provisional",
            "confidence": 70,
            "description": "Temporary browser verification feed",
            "evidence_json": '{"source":"browser-gate-feed","confidence":70}',
        },
        "/catalog-feeds",
        "catalog feeds create",
        result,
    )
    edit_path = find_table_edit_path(
        client,
        urllib.parse.urljoin(base_url, f"/catalog-feeds?q={urllib.parse.quote(tvg_id)}"),
        "/catalog-feeds/table",
        tvg_id,
        "/catalog-feeds/",
        "catalog feeds created row",
        result,
    )
    return edit_path, tvg_id


def assert_catalog_feed_edit_and_delete(client: CDPClient, base_url: str, edit_path: str, result: VerificationResult) -> None:
    """验证临时 CatalogFeed 编辑页结构、远程 select 回显、上传预览和删除流程。"""
    if not edit_path:
        return
    navigate(client, urllib.parse.urljoin(base_url, edit_path))
    edit_assertion = client.evaluate(
        js_backend_page_assertions(
            edit_path,
            ["Edit Catalog Feed", "Catalog Channel", "Logo Upload Preview", "Danger Zone", "Delete"],
            ["form[method='post']", "select[name='catalog_channel_id']", "[data-om-component='upload']", "button.om-button-danger, button.btn-danger"],
            "/catalog-feeds",
        ),
        timeout=10.0,
    )
    edit_assertion_failures = assertion_failures(edit_assertion)
    result.pageErrors.extend(str(failure) for failure in edit_assertion_failures)
    assert_visual_health(client, "catalog feeds edit", result)
    assert_catalog_feed_remote_channel_select(client, "catalog feeds edit catalog channel select", result)
    assert_catalog_feed_upload_preview(client, "catalog feeds edit", result)
    assert_delete_form_redirects(client, base_url, edit_path, "/catalog-feeds", "catalog feeds delete", result)


def assert_form_ajax_validation(client: CDPClient, form_path: str, field_name: str, result: VerificationResult) -> None:
    """提交真实业务表单并验证校验错误通过 AJAX 回显且没有整页刷新。"""
    before = install_request_probe(client)
    before_path, before_origin, before_probe, before_navigation_count = required_request_probe_identity(
        before,
        f"{form_path} AJAX validation before",
    )
    if before_path != form_path:
        raise VerificationError(f"{form_path} AJAX validation before path does not match form path")
    clear_request_probe(client)
    submit_result = client.evaluate(
        r"""
(async () => {
  const fieldName =
"""
        + json.dumps(field_name)
        + r""";
  const failures = [];
  const form = document.querySelector("form[data-om-form]");
  const field = form?.querySelector(`[name="${CSS.escape(fieldName)}"]`);
  if (!form) failures.push("missing data-om-form");
  if (!field) failures.push(`missing form field: ${fieldName}`);
  if (form?.dataset.omFormMode === "html") failures.push("business form should default to json mode");
  if (failures.length) return { failures };
  for (const input of form.querySelectorAll("input:not([type='hidden']), textarea")) {
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    submit_result_failures = assertion_failures(submit_result)
    result.pageErrors.extend(f"{form_path} form: {failure}" for failure in submit_result_failures)
    if submit_result_failures:
        return

    deadline = time.monotonic() + 8.0
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const formPath =
"""
            + json.dumps(form_path)
            + r""";
  const fieldName =
"""
            + json.dumps(field_name)
            + r""";
  const navigationEntries = performance.getEntriesByType("navigation");
  const requests = window.__oldmanRequestLog || [];
  const matched = requests.find((request) => {
    const url = new URL(request.url, location.href);
    return url.pathname === formPath && String(request.method || "GET").toUpperCase() === "POST";
  });
  const error = document.querySelector(`[data-om-error-for="${CSS.escape(fieldName)}"]`);
  return {
    path: location.pathname,
    origin: location.origin,
    probe: window.__oldmanPageProbe || "",
    navigationCount: navigationEntries.length,
    requests,
    matched: Boolean(matched),
    status: document.querySelector("[data-om-component='form']")?.dataset.omStatus || "",
    hasErrorText: Boolean(error && error.textContent.trim().length > 0),
    fieldInvalid: document.querySelector(`[name="${CSS.escape(fieldName)}"]`)?.getAttribute("aria-invalid") || "",
  };
})()
""",
                timeout=5.0,
            ),
            f"{form_path} AJAX validation state",
        )
        last_state = state
        path, origin, probe, navigation_count = required_request_probe_identity(
            state,
            f"{form_path} AJAX validation state",
        )
        requests = required_request_records(state, f"{form_path} AJAX validation state")
        matched = required_success_bool(state, "matched", f"{form_path} AJAX validation state")
        status = required_string_field(state, "status", f"{form_path} AJAX validation state")
        has_error_text = required_success_bool(state, "hasErrorText", f"{form_path} AJAX validation state")
        field_invalid = required_string_field(state, "fieldInvalid", f"{form_path} AJAX validation state")
        recomputed_match = has_matching_request(requests, origin, form_path, method="POST")
        if matched is not recomputed_match:
            raise VerificationError(f"{form_path} AJAX validation matched disagrees with requests")
        if (
            matched
            and path == before_path
            and origin == before_origin
            and probe == before_probe
            and navigation_count == before_navigation_count
            and status == "error"
            and has_error_text
            and field_invalid == "true"
        ):
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{form_path} form: AJAX 校验错误回显未通过，最后状态 {last_state}")


def assert_form_html_fragment_validation(client: CDPClient, form_path: str, field_name: str, result: VerificationResult) -> None:
    """提交真实业务表单 HTML 模式，并验证表单片段局部替换且没有整页刷新。"""
    before = install_request_probe(client)
    before_path, before_origin, before_probe, before_navigation_count = required_request_probe_identity(
        before,
        f"{form_path} HTML validation before",
    )
    if before_path != form_path:
        raise VerificationError(f"{form_path} HTML validation before path does not match form path")
    clear_request_probe(client)
    submit_result = client.evaluate(
        r"""
(async () => {
  const fieldName =
"""
        + json.dumps(field_name)
        + r""";
  const failures = [];
  const form = document.querySelector("form[data-om-form]");
  const field = form?.querySelector(`[name="${CSS.escape(fieldName)}"]`);
  if (!form) failures.push("missing data-om-form");
  if (!field) failures.push(`missing form field: ${fieldName}`);
  if (failures.length) return { failures };
  form.setAttribute("data-om-form-mode", "html");
  for (const input of form.querySelectorAll("input:not([type='hidden']), textarea")) {
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    submit_result_failures = assertion_failures(submit_result)
    result.pageErrors.extend(f"{form_path} html form: {failure}" for failure in submit_result_failures)
    if submit_result_failures:
        return

    deadline = time.monotonic() + 8.0
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const formPath =
"""
            + json.dumps(form_path)
            + r""";
  const fieldName =
"""
            + json.dumps(field_name)
            + r""";
  const navigationEntries = performance.getEntriesByType("navigation");
  const requests = window.__oldmanRequestLog || [];
  const matched = requests.find((request) => {
    const url = new URL(request.url, location.href);
    return url.pathname === formPath
      && String(request.method || "GET").toUpperCase() === "POST"
      && String(request.headers?.accept || "").includes("text/html");
  });
  const forms = document.querySelectorAll("form[data-om-form]");
  const form = forms[0] || null;
  const error = form?.querySelector(`[data-om-error-for="${CSS.escape(fieldName)}"]`);
  return {
    path: location.pathname,
    origin: location.origin,
    probe: window.__oldmanPageProbe || "",
    navigationCount: navigationEntries.length,
    requests,
    matched: Boolean(matched),
    formCount: forms.length,
    nestedForm: Boolean(form?.querySelector("form")),
    hasErrorText: Boolean(error && error.textContent.trim().length > 0),
    fieldInvalid: form?.querySelector(`[name="${CSS.escape(fieldName)}"]`)?.getAttribute("aria-invalid") || "",
  };
})()
""",
                timeout=5.0,
            ),
            f"{form_path} HTML validation state",
        )
        last_state = state
        path, origin, probe, navigation_count = required_request_probe_identity(
            state,
            f"{form_path} HTML validation state",
        )
        requests = required_request_records(state, f"{form_path} HTML validation state")
        matched = required_success_bool(state, "matched", f"{form_path} HTML validation state")
        form_count = required_non_negative_integer(state, "formCount", f"{form_path} HTML validation state")
        nested_form = required_success_bool(state, "nestedForm", f"{form_path} HTML validation state")
        has_error_text = required_success_bool(state, "hasErrorText", f"{form_path} HTML validation state")
        field_invalid = required_string_field(state, "fieldInvalid", f"{form_path} HTML validation state")
        recomputed_match = has_matching_request(
            requests,
            origin,
            form_path,
            method="POST",
            accept_contains="text/html",
        )
        if matched is not recomputed_match:
            raise VerificationError(f"{form_path} HTML validation matched disagrees with requests")
        if (
            matched
            and path == before_path
            and origin == before_origin
            and probe == before_probe
            and navigation_count == before_navigation_count
            and form_count == 1
            and nested_form is False
            and has_error_text
            and field_invalid == "true"
        ):
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{form_path} html form: HTML 片段局部替换未通过，最后状态 {last_state}")


def assert_remote_select_loaded(client: CDPClient, provider_path: str, select_selector: str, label: str, result: VerificationResult) -> None:
    """验证远程 Select 已请求 provider endpoint 并渲染候选项。"""
    deadline = time.monotonic() + 8.0
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const providerPath =
"""
            + json.dumps(provider_path)
            + r""";
  const selector =
"""
            + json.dumps(select_selector)
            + r""";
  const select = document.querySelector(selector);
  const requested = performance.getEntriesByType("resource").some((entry) => {
    const url = new URL(entry.name, location.href);
    return url.pathname === providerPath;
  });
  return {
    requested,
    hasSelect: Boolean(select),
    options: select ? Array.from(select.options).map((option) => option.textContent || "") : [],
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} remote select state",
        )
        last_state = state
        requested = required_success_bool(state, "requested", f"{label} remote select state")
        has_select = required_success_bool(state, "hasSelect", f"{label} remote select state")
        options = state.get("options")
        if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
            raise VerificationError(f"{label} remote select state payload options must be a list of strings")
        if requested and has_select and options:
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: 远程 Select provider 加载未通过，最后状态 {last_state}")


def assert_remote_select_search(client: CDPClient, provider_path: str, select_selector: str, query: str, label: str, result: VerificationResult) -> None:
    """验证远程 Select 搜索只请求 provider endpoint，且不刷新页面。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    search_result = client.evaluate(
        r"""
(async () => {
  const selector =
"""
        + json.dumps(select_selector)
        + r""";
  const query =
"""
        + json.dumps(query)
        + r""";
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const select = document.querySelector(selector);
  const failures = [];
  if (!select) failures.push(`missing remote select: ${selector}`);
  const choicesInput = select?.closest(".choices")?.querySelector(".choices__input--cloned")
    || select?.parentElement?.querySelector(".choices__input--cloned");
  if (!choicesInput) failures.push("missing Choices search input for remote select");
  if (failures.length) return { failures };

  choicesInput.focus();
  choicesInput.value = query;
  choicesInput.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: query }));
  await sleep(200);
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    search_result_failures = assertion_failures(search_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in search_result_failures)
    if search_result_failures:
        return
    wait_for_endpoint_request(client, provider_path, {"q": query}, before, label, result, noun="远程 Select 搜索请求")


def assert_remote_select_pagination(client: CDPClient, provider_path: str, select_selector: str, label: str, result: VerificationResult) -> None:
    """验证远程 Select provider 支持分页参数并能读取下一页。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    pagination_result = client.evaluate(
        r"""
(async () => {
  const providerPath =
"""
        + json.dumps(provider_path)
        + r""";
  const selector =
"""
        + json.dumps(select_selector)
        + r""";
  const failures = [];
  const select = document.querySelector(selector);
  if (!select) return { failures: [`missing remote select: ${selector}`] };
  const source = select.getAttribute("data-om-select-src");
  const bind = select.getAttribute("data-om-select-bind");
  if (!source || !source.includes(providerPath)) failures.push("remote select source does not match provider");
  if (!bind) failures.push("remote select bind token missing");
  if (failures.length) return { failures };

  const pageOne = new URL(source, location.href);
  pageOne.searchParams.set("bind", bind);
  pageOne.searchParams.set("page", "1");
  pageOne.searchParams.set("page_size", "1");
  const firstResponse = await fetch(pageOne.toString(), { headers: { Accept: "application/json" } });
  const firstPayload = await firstResponse.json();
  if (!firstResponse.ok) failures.push(`page 1 returned ${firstResponse.status}`);
  if (!Array.isArray(firstPayload.results)) failures.push("page 1 results is not an array");
  if (firstPayload.more !== true) failures.push("page 1 did not advertise more=true with page_size=1");

  const pageTwo = new URL(source, location.href);
  pageTwo.searchParams.set("bind", bind);
  pageTwo.searchParams.set("page", "2");
  pageTwo.searchParams.set("page_size", "1");
  const secondResponse = await fetch(pageTwo.toString(), { headers: { Accept: "application/json" } });
  const secondPayload = await secondResponse.json();
  if (!secondResponse.ok) failures.push(`page 2 returned ${secondResponse.status}`);
  if (!Array.isArray(secondPayload.results)) failures.push("page 2 results is not an array");
  if (Array.isArray(secondPayload.results) && secondPayload.results.length < 1) failures.push("page 2 returned no result");
  return { failures };
})()
""",
        timeout=8.0,
    )
    pagination_result_failures = assertion_failures(pagination_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in pagination_result_failures)
    if not pagination_result_failures:
        wait_for_endpoint_request(client, provider_path, {"page": "2", "page_size": "1"}, before, label, result, noun="远程 Select 分页请求")


def assert_remote_autocomplete_search(client: CDPClient, provider_path: str, input_selector: str, query: str, label: str, result: VerificationResult) -> None:
    """验证真实业务 Autocomplete 搜索只请求 provider endpoint，且不刷新页面。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    search_result = client.evaluate(
        r"""
(async () => {
  const selector =
"""
        + json.dumps(input_selector)
        + r""";
  const query =
"""
        + json.dumps(query)
        + r""";
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const input = document.querySelector(selector);
  const root = input?.closest("[data-om-component='autocomplete']");
  const failures = [];
  if (!input) failures.push(`missing autocomplete input: ${selector}`);
  if (!root) failures.push("missing autocomplete root");
  if (failures.length) return { failures };

  input.focus();
  input.value = query;
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: query }));
  await sleep(250);
  const items = root.querySelectorAll("[data-om-autocomplete-item]");
  if (items.length === 0) failures.push("autocomplete provider did not render any suggestions");
  return { failures };
})()
""",
        timeout=5.0,
    )
    search_result_failures = assertion_failures(search_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in search_result_failures)
    if search_result_failures:
        return
    wait_for_endpoint_request(client, provider_path, {"q": query}, before, label, result, noun="远程 Autocomplete 搜索请求")


def assert_dashboard_programme_chart(client: CDPClient, result: VerificationResult) -> None:
    """验证首页三个业务图表真实挂载、渲染状态和 fallback 节点。"""
    for label, path in (
        ("programme trend chart", "/dashboard/charts/programme-trend"),
        ("feed status chart", "/dashboard/charts/feed-status"),
        ("logo quality chart", "/dashboard/charts/logo-quality"),
    ):
        assert_dashboard_chart(client, result, label, path)


def assert_dashboard_chart(client: CDPClient, result: VerificationResult, label: str, path: str) -> None:
    """验证单个 dashboard 图表 shell、成功/空态和错误 fallback。"""
    selector_payload = json.dumps(path)
    deadline = time.monotonic() + 10.0
    last_state: dict[str, Any] = {}
    status = ""
    has_svg = False
    empty_visible = False
    empty_text = ""
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const chartSrc =
"""
            + selector_payload
            + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='apex-chart']")).find((element) => element.getAttribute("data-om-chart-src") === chartSrc);
  const target = root?.querySelector("[data-om-chart-target]");
  const empty = root?.querySelector("[data-om-chart-empty]");
  const error = root?.querySelector("[data-om-chart-error]");
  const svg = root?.querySelector(".apexcharts-svg");
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  return {
    exists: Boolean(root),
    targetExists: Boolean(target),
    emptyExists: Boolean(empty),
    errorExists: Boolean(error),
    status: root?.dataset.omStatus || "",
    hasSvg: Boolean(svg),
    emptyVisible: visible(empty),
    emptyText: empty?.textContent?.trim() || "",
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} chart state",
        )
        last_state = state
        exists = required_success_bool(state, "exists", f"{label} chart state")
        target_exists = required_success_bool(state, "targetExists", f"{label} chart state")
        empty_exists = required_success_bool(state, "emptyExists", f"{label} chart state")
        error_exists = required_success_bool(state, "errorExists", f"{label} chart state")
        status = required_string_field(state, "status", f"{label} chart state")
        has_svg = required_success_bool(state, "hasSvg", f"{label} chart state")
        empty_visible = required_success_bool(state, "emptyVisible", f"{label} chart state")
        empty_text = required_string_field(state, "emptyText", f"{label} chart state")
        if not exists:
            result.pageErrors.append(f"{label}: missing apex-chart shell")
            return
        if not target_exists:
            result.pageErrors.append(f"{label}: missing data-om-chart-target")
            return
        if not empty_exists:
            result.pageErrors.append(f"{label}: missing data-om-chart-empty")
            return
        if not error_exists:
            result.pageErrors.append(f"{label}: missing data-om-chart-error")
            return
        if status in {"success", "empty", "error"}:
            break
        time.sleep(0.1)
    else:
        result.pageErrors.append(f"{label}: 图表未完成挂载，最后状态 {last_state}")
        return

    if status == "success":
        if not has_svg:
            result.pageErrors.append(f"{label}: success 状态没有 ApexCharts SVG，状态 {last_state}")
        if empty_visible or empty_text:
            result.pageErrors.append(f"{label}: success 状态仍显示或保留 empty 内容，状态 {last_state}")
    elif status == "empty":
        if not empty_visible or not empty_text:
            result.pageErrors.append(f"{label}: empty 状态没有可见空态，状态 {last_state}")
        if has_svg:
            result.pageErrors.append(f"{label}: empty 状态仍保留 ApexCharts SVG，状态 {last_state}")
    elif status == "error":
        result.pageErrors.append(f"{label}: 远程图表请求失败，状态 {last_state}")

    fallback_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const chartSrc =
"""
        + selector_payload
        + r""";
  const root = Array.from(document.querySelectorAll("[data-om-component='apex-chart']")).find((element) => element.getAttribute("data-om-chart-src") === chartSrc);
  const error = root?.querySelector("[data-om-chart-error]");
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  if (!error) return { failures: ["missing data-om-chart-error"] };
  const previousHidden = error.hidden;
  const previousText = error.textContent || "";
  error.hidden = false;
  error.textContent = "Chart error fallback";
  if (!visible(error)) failures.push("data-om-chart-error fallback is not visible when shown");
  if (!error.textContent.trim()) failures.push("data-om-chart-error fallback has no message");
  error.hidden = previousHidden;
  error.textContent = previousText;
  return { failures };
})()
""",
        timeout=5.0,
    )
    fallback_result_failures = assertion_failures(fallback_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in fallback_result_failures)


def assert_dashboard_chart_loading_overlay_3g(port: int, base_url: str, result: VerificationResult) -> None:
    """在独立页面目标中验证 3G 图表遮罩，避免慢网状态污染后续门禁。"""
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
    """在 3G 慢网和延迟 chart XHR 下，图表区域必须立即显示 scoped 遮罩。"""
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
            raise VerificationError(
                f"dashboard chart 3g Page.navigate failed: {error_text}"
            )
        client.pump(0.5)

        deadline = time.monotonic() + 8.0
        last_state: dict[str, Any] = {}
        last_failures: list[str] = []
        while time.monotonic() < deadline:
            raw_state = client.evaluate(
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
            )
            failures = assertion_failures(raw_state)
            state = required_payload_object(raw_state, "dashboard chart 3g loading state")
            last_state = state
            last_failures = failures
            fullscreen_visible = required_success_bool(state, "fullscreenVisible", "dashboard chart 3g loading state")
            chart_count = required_non_negative_integer(state, "chartCount", "dashboard chart 3g loading state")
            loading_scope_count = required_non_negative_integer(state, "loadingScopeCount", "dashboard chart 3g loading state")
            loading_count = required_non_negative_integer(state, "loadingCount", "dashboard chart 3g loading state")
            overlay_count = required_non_negative_integer(state, "overlayCount", "dashboard chart 3g loading state")
            if loading_scope_count > chart_count or loading_count > loading_scope_count or overlay_count > loading_scope_count:
                raise VerificationError("dashboard chart 3g loading state has impossible count boundaries")
            if not failures and len({chart_count, loading_scope_count, loading_count, overlay_count}) != 1:
                raise VerificationError("dashboard chart 3g loading state does not cover every chart")
            if (
                not fullscreen_visible
                and not failures
                and chart_count >= 3
                and loading_scope_count >= 3
                and loading_count >= 3
                and overlay_count >= 3
            ):
                capture_named_screenshot(
                    client,
                    result,
                    "desktop-dashboard-chart-loading-3g",
                    "/tmp/oldman-dashboard-chart-loading-3g.png",
                )
                break
            time.sleep(0.1)
        else:
            result.pageErrors.extend(f"dashboard chart 3g loading: {failure}" for failure in last_failures)
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


def assert_visible_modal_fits(client: CDPClient, result: VerificationResult, label: str, selector: str = ".om-modal.is-open, .modal.show") -> None:
    """验证当前可见 modal 和主要内容没有溢出弹窗边界。"""
    fit_result = client.evaluate(
        r"""
(() => {
  const selector =
"""
        + json.dumps(selector)
        + r""";
  const failures = [];
  const allowance = 2;
  const modal = document.querySelector(selector);
  const content = modal?.querySelector(".om-modal-surface, .modal-content");
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  if (!modal || !visible(modal)) return { failures: [`missing visible modal ${selector}`] };
  if (!content || !visible(content)) return { failures: [`missing visible modal content ${selector}`] };

  const contentRect = content.getBoundingClientRect();
  if (contentRect.left < -allowance || contentRect.right > window.innerWidth + allowance) {
    failures.push(`modal content exceeds viewport: left=${contentRect.left}, right=${contentRect.right}, viewport=${window.innerWidth}`);
  }

  const body = modal.querySelector(".om-modal-body, .modal-body") || content;
  const header = modal.querySelector(".om-modal-header");
  const footer = modal.querySelector(".om-modal-footer");
  const near = (actual, expected) => Math.abs(parseFloat(actual) - expected) <= 0.5;
  if (!header) failures.push("modal is missing shared .om-modal-header");
  else if (!near(getComputedStyle(header).marginBottom, 16)) failures.push(`modal header margin-bottom drifted: ${getComputedStyle(header).marginBottom}`);
  const bodyStyle = getComputedStyle(body);
  if (!near(bodyStyle.fontSize, 14)) failures.push(`modal body font-size drifted: ${bodyStyle.fontSize}`);
  if (!near(bodyStyle.lineHeight, 24)) failures.push(`modal body line-height drifted: ${bodyStyle.lineHeight}`);
  if (!footer) failures.push("modal is missing shared .om-modal-footer");
  else if (!near(getComputedStyle(footer).marginTop, 24)) failures.push(`modal footer margin-top drifted: ${getComputedStyle(footer).marginTop}`);
  for (const element of Array.from(body.querySelectorAll("*"))) {
    if (!visible(element)) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (rect.left < contentRect.left - allowance || rect.right > contentRect.right + allowance) {
      const text = (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 80);
      failures.push(`modal child overflows content: <${element.tagName.toLowerCase()} class="${element.className}"> ${text}`);
      break;
    }
  }
  return { failures };
})()
""",
        timeout=5.0,
    )
    fit_result_failures = assertion_failures(fit_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in fit_result_failures)


def assert_dashboard_overview_interactions(client: CDPClient, result: VerificationResult) -> None:
    """验证首页 range、refresh toast、失败反馈和顶栏通知交互。"""
    install_request_probe(client)
    interaction_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const state = () => {
    const root = document.querySelector("[data-om-component='dashboard-overview']");
    return {
      mounted: root?.dataset.omDashboardOverviewMounted || "",
      lastAction: root?.dataset.omDashboardLastAction || "",
      chartCount: root?.dataset.omDashboardChartCount || "",
      requests: (window.__oldmanRequestLog || []).map((request) => String(request.url)).slice(-8)
    };
  };
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility === "visible" && rect.width > 0 && rect.height > 0;
  };
  const requestedChartPaths = (range) => {
    const paths = new Set();
    for (const request of window.__oldmanRequestLog || []) {
      const url = new URL(request.url, location.href);
      if (url.searchParams.get("range") === range) paths.add(url.pathname);
    }
    return paths;
  };
  const clearFeedbackOverlays = () => {
    document.querySelector(".swal2-close, .swal2-confirm")?.click();
    document.querySelectorAll(".swal2-container").forEach((element) => element.remove());
    document.body.classList.remove("swal2-shown", "swal2-height-auto", "swal2-toast-shown");
    document.documentElement.classList.remove("swal2-shown", "swal2-height-auto", "swal2-toast-shown");
    document.body.style.removeProperty("overflow");
    document.body.style.removeProperty("padding-right");
  };

  window.__oldmanRequestLog = [];
  const range = document.querySelector("[data-om-dashboard-range='7d']");
  if (!range) {
    failures.push("missing data-om-dashboard-range");
  } else {
    range.click();
    for (let index = 0; index < 40; index += 1) {
      const paths = requestedChartPaths("7d");
      if (
        paths.has("/dashboard/charts/programme-trend")
        && paths.has("/dashboard/charts/feed-status")
        && paths.has("/dashboard/charts/logo-quality")
      ) break;
      await sleep(100);
    }
    const paths = requestedChartPaths("7d");
    for (const path of ["/dashboard/charts/programme-trend", "/dashboard/charts/feed-status", "/dashboard/charts/logo-quality"]) {
      if (!paths.has(path)) failures.push(`range dropdown did not request ${path} range=7d: ${JSON.stringify(state())}`);
    }
  }

  window.__oldmanRequestLog = [];
  const refresh = document.querySelector("[data-om-dashboard-refresh]");
  if (!refresh) {
    failures.push("missing data-om-dashboard-refresh");
  } else {
    refresh.click();
    for (let index = 0; index < 40 && !document.querySelector(".toastify.om-toast.on"); index += 1) {
      await sleep(100);
    }
    const toast = document.querySelector(".toastify.om-toast.on");
    if (!toast || !toast.textContent.includes("Dashboard refreshed")) failures.push("refresh did not show dashboard success toast");
  }

  clearFeedbackOverlays();
  await sleep(100);
  window.__oldmanRequestLog = [];
  const invalidRange = document.createElement("button");
  invalidRange.type = "button";
  invalidRange.hidden = true;
  invalidRange.dataset.omDashboardRange = "invalid";
  document.querySelector("[data-om-component='dashboard-overview']")?.appendChild(invalidRange);
  invalidRange.click();
  for (let index = 0; index < 40; index += 1) {
    const paths = requestedChartPaths("invalid");
    const popup = document.querySelector(".swal2-popup");
    if (
      popup?.textContent?.includes("Chart request failed") ||
      (
        paths.has("/dashboard/charts/programme-trend")
        && paths.has("/dashboard/charts/feed-status")
        && paths.has("/dashboard/charts/logo-quality")
      )
    ) break;
    await sleep(100);
  }
  const popup = document.querySelector(".swal2-popup");
  const invalidPaths = requestedChartPaths("invalid");
  for (const path of ["/dashboard/charts/programme-trend", "/dashboard/charts/feed-status", "/dashboard/charts/logo-quality"]) {
    if (!invalidPaths.has(path)) failures.push(`chart failure did not request ${path} invalid range`);
  }
  if (!popup || !popup.textContent.includes("Chart request failed")) failures.push("chart failure did not show feedback alert");
  clearFeedbackOverlays();
  await sleep(100);
  invalidRange.remove();

  const notificationToggle = document.querySelector("#page-header-notifications-dropdown");
  const notificationDropdown = document.querySelector("#notificationDropdown");
  if (!notificationToggle || !notificationDropdown) {
    failures.push("missing notificationDropdown");
  } else {
    notificationToggle.click();
    await sleep(100);
    const menu = notificationDropdown.querySelector("[data-om-dropdown-menu], .dropdown-menu");
    if (!visible(menu)) failures.push("notification dropdown did not open");
    const firstCheck = notificationDropdown.querySelector(".notification-check input");
    if (!firstCheck) {
      failures.push("missing notification-check");
    } else {
      firstCheck.checked = true;
      firstCheck.dispatchEvent(new Event("change", { bubbles: true }));
      if (!firstCheck.closest(".notification-item")?.classList.contains("active")) failures.push("notification item did not become active");
      if (!visible(document.querySelector("[data-om-activity-notification-actions]"))) failures.push("notification actions did not show after selection");
      const beforeCount = notificationDropdown.querySelectorAll(".notification-item").length;
      const removeButton = document.querySelector('[data-om-modal-target="#removeNotificationModal"], [data-bs-target="#removeNotificationModal"]');
      const deleteButton = document.querySelector("#delete-notification");
      const modal = document.querySelector("#removeNotificationModal");
      if (!removeButton || !deleteButton || !modal) {
        failures.push("missing notification removal modal controls");
      } else {
        removeButton.click();
        for (let index = 0; index < 40 && !visible(modal); index += 1) {
          await sleep(100);
        }
        if (!visible(modal)) failures.push("removeNotificationModal did not open");
        deleteButton.click();
        await sleep(100);
        const afterCount = notificationDropdown.querySelectorAll(".notification-item").length;
        if (afterCount >= beforeCount) failures.push("delete-notification did not remove selected item");
      }
    }
  }

  return { failures };
})()
""",
        timeout=15.0,
    )
    interaction_result_failures = assertion_failures(interaction_result)
    result.pageErrors.extend(f"dashboard overview interactions: {failure}" for failure in interaction_result_failures)


def configure_viewport(client: CDPClient, width: int, height: int, mobile: bool) -> None:
    """Set Chrome's viewport and device metrics for the next capture."""
    client.command(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": 2 if mobile else 1,
            "mobile": mobile,
        },
    )
    client.command("Emulation.setVisibleSize", {"width": width, "height": height})


def navigate(client: CDPClient, url: str, *, wait_for_runtime: bool = True) -> None:
    """跳转页面并等待新文档加载；普通页面继续等待 Oldman 运行时挂载。"""
    navigation_probe = str(uuid.uuid4())
    client.evaluate(
        f"window.__oldmanNavigationProbe = {json.dumps(navigation_probe)}; true",
        timeout=5.0,
    )
    client.load_seen = False
    navigation_result = client.command("Page.navigate", {"url": url})
    error_text = navigation_result.get("errorText")
    if error_text:
        raise VerificationError(f"Page.navigate failed for {url}: {error_text}")
    try:
        client.wait_for_load(timeout=8.0)
    except PageLoadTimeout as error:
        fallback_state = required_payload_object(
            client.evaluate(
                "(() => ({ ready: document.readyState, href: location.href }))()",
                timeout=5.0,
            ),
            "navigate load-timeout fallback",
        )
        ready_state = required_string_field(fallback_state, "ready", "navigate load-timeout fallback")
        current_url = required_success_string(fallback_state, "href", "navigate load-timeout fallback")
        if ready_state != "complete" or not same_origin_path_query(current_url, url):
            raise VerificationError(
                f"Page load timeout fallback did not reach target {url}: ready={ready_state!r}, href={current_url!r}"
            ) from error
    client.evaluate(
        "new Promise(resolve => { if (document.readyState === 'complete') resolve(true); else window.addEventListener('load', () => resolve(true), { once: true }); })",
        timeout=5.0,
    )
    if not wait_for_runtime:
        client.pump(0.5)
        return

    ready_state = required_payload_object(
        client.evaluate(
            r"""
(async () => {
  const expectedProbe =
"""
            + json.dumps(navigation_probe)
            + r""";
  for (let index = 0; index < 100; index += 1) {
    const state = {
      probe: window.__oldmanNavigationProbe || "",
      ready: document.readyState,
      href: location.href,
      omReady: document.documentElement.dataset.omReady || "",
    };
    if (state.probe !== expectedProbe && state.ready === "complete" && state.omReady === "true") return state;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return {
    probe: window.__oldmanNavigationProbe || "",
    ready: document.readyState,
    href: location.href,
    omReady: document.documentElement.dataset.omReady || "",
  };
})()
""",
            timeout=12.0,
        ),
        "navigate runtime-ready state",
    )
    probe = required_string_field(ready_state, "probe", "navigate runtime-ready state")
    ready = required_string_field(ready_state, "ready", "navigate runtime-ready state")
    href = required_success_string(ready_state, "href", "navigate runtime-ready state")
    om_ready = required_string_field(ready_state, "omReady", "navigate runtime-ready state")
    if probe == navigation_probe or ready != "complete" or om_ready != "true" or not same_origin_path_query(href, url):
        raise VerificationError(f"Navigation did not reach a runtime-ready target {url}: {ready_state}")
    client.pump(0.5)


def clear_browser_state(client: CDPClient, base_url: str) -> None:
    """清理当前验证 origin 的浏览器状态，确保登录门禁从匿名态开始。"""
    parsed = urllib.parse.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    client.command("Network.clearBrowserCookies")
    client.command("Network.clearBrowserCache")
    client.command("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})


def logout_browser_session(client: CDPClient, base_url: str) -> None:
    """访问退出登录端点，释放可能残留的服务端 session。"""
    client.load_seen = False
    client.command("Page.navigate", {"url": urllib.parse.urljoin(base_url, "/logout")})
    try:
        client.wait_for_load(timeout=8.0)
    except PageLoadTimeout:
        client.pump(1.0)
    client.pump(0.25)


def login(client: CDPClient, base_url: str, username: str, password: str) -> None:
    """通过真实登录表单进入后台。"""
    logout_browser_session(client, base_url)
    clear_browser_state(client, base_url)
    login_url = urllib.parse.urljoin(base_url, "/login")
    navigate(client, login_url)
    credentials_json = json.dumps({"username": username, "password": password})
    client.load_seen = False
    result = client.evaluate(
        """
        (async () => {
          const credentials =
        """
        + credentials_json
        + r""";
          const failures = [];
          const usernameInput = document.querySelector('input[name="username"]');
          const passwordInput = document.querySelector('input[name="password"]');
          const form = document.querySelector('form[action="/login"]');
          const path = location.pathname;
          const hasAuthenticatedShell = Boolean(
            document.querySelector("#oldman-sidebar-nav") &&
            document.querySelector("#page-topbar") &&
            document.querySelector("#oldman-main")
          );
          // 已登录时保持当前有效会话，避免长流程刷新登录时误报缺少登录表单。
          if (path !== "/login" && !form) {
            return { failures: [], branch: "already-authenticated", path, hasAuthenticatedShell, submitted: false };
          }
          if (!usernameInput) failures.push("missing username input");
          if (!passwordInput) failures.push("missing password input");
          if (!form) failures.push("missing login form");
          if (failures.length) return { failures };
          usernameInput.value = credentials.username;
          passwordInput.value = credentials.password;
          usernameInput.dispatchEvent(new Event("input", { bubbles: true }));
          passwordInput.dispatchEvent(new Event("input", { bubbles: true }));
          form.submit();
          return { failures: [], branch: "submitted", path, hasAuthenticatedShell, submitted: true };
        })()
        """,
        timeout=5.0,
    )
    failures = assertion_failures(result)
    if failures:
        raise VerificationError("; ".join(failures))

    branch = required_success_string(result, "branch", "login")
    path = required_success_string(result, "path", "login")
    has_authenticated_shell = required_success_bool(result, "hasAuthenticatedShell", "login")
    submitted = required_success_bool(result, "submitted", "login")
    if branch == "already-authenticated":
        if path == "/login" or not path.startswith("/"):
            raise VerificationError("login already-authenticated payload path must be a non-login absolute path")
        if has_authenticated_shell is not True or submitted is not False:
            raise VerificationError("login already-authenticated payload has inconsistent shell/submitted state")
        client.pump(0.5)
        return
    if branch != "submitted":
        raise VerificationError(f"login success payload has unsupported branch {branch}")
    if path != "/login" or has_authenticated_shell is not False or submitted is not True:
        raise VerificationError("login submitted payload has inconsistent path/shell/submitted state")
    client.wait_for_load()
    client.pump(0.5)


def save_screenshot(client: CDPClient, path: str) -> None:
    """Capture a PNG screenshot from Chrome and write it to path."""
    data = client.command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=10.0).get("data")
    if not data:
        raise VerificationError(f"Chrome returned no screenshot data for {path}")
    output = screenshot_output_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(data))


def screenshot_output_path(path: str) -> Path:
    """Keep wrapper-owned screenshots inside its isolated run directory."""
    output = Path(path).expanduser()
    configured = os.environ.get("OLDMAN_EPG_CHILD_SCREENSHOT_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
        resolved = output.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            output = root / output.name
        else:
            output = resolved
    return output.resolve()


def capture_named_screenshot(client: CDPClient, result: VerificationResult, name: str, path: str) -> None:
    """截图并记录到结构化结果，便于人工抽查不同页面和视口。"""
    output = screenshot_output_path(path)
    save_screenshot(client, str(output))
    result.screenshots[name] = str(output)


def wait_for_path(client: CDPClient, expected_path: str, label: str, result: VerificationResult, timeout: float = 10.0) -> None:
    """等待当前页面到达指定 path。"""
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => ({
  path: location.pathname,
  ready: document.readyState,
  omReady: document.documentElement?.dataset.omReady || ""
}))()
""",
                timeout=5.0,
            ),
            f"{label} path state",
        )
        last_state = state
        path = required_string_field(state, "path", f"{label} path state")
        ready = required_string_field(state, "ready", f"{label} path state")
        required_string_field(state, "omReady", f"{label} path state")
        if path == expected_path and ready == "complete":
            client.pump(0.3)
            return
        time.sleep(0.1)
    result.pageErrors.append(f"{label}: 等待页面跳转到 {expected_path} 未通过，最后状态 {last_state}")


def submit_business_form(client: CDPClient, path: str, values: dict[str, object], expected_path: str, label: str, result: VerificationResult) -> None:
    """填充并提交真实业务表单，等待成功 redirect。"""
    before = install_request_probe(client)
    clear_request_probe(client)
    client.load_seen = False
    submit_result = client.evaluate(
        r"""
(async () => {
  const values =
"""
        + json.dumps(values, ensure_ascii=False)
        + r""";
  const failures = [];
  const form = document.querySelector("form[data-om-form]");
  if (!form) return { failures: ["missing data-om-form"] };
  for (const [name, value] of Object.entries(values)) {
    const control = form.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!control) {
      failures.push(`missing form field: ${name}`);
      continue;
    }
    if (control instanceof HTMLInputElement && control.type === "checkbox") {
      control.checked = Boolean(value);
      control.dispatchEvent(new Event("change", { bubbles: true }));
      continue;
    }
    if (control instanceof HTMLSelectElement) {
      const stringValue = String(value);
      if (!Array.from(control.options).some((option) => option.value === stringValue)) {
        control.append(new Option(stringValue, stringValue, true, true));
      }
      control.value = stringValue;
      control.dispatchEvent(new Event("change", { bubbles: true }));
      continue;
    }
    control.value = String(value);
    control.dispatchEvent(new Event("input", { bubbles: true }));
    control.dispatchEvent(new Event("change", { bubbles: true }));
  }
  if (failures.length) return { failures };
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    submit_result_failures = assertion_failures(submit_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in submit_result_failures)
    if submit_result_failures:
        return

    wait_for_form_post_and_redirect(client, path, expected_path, before, label, result)


def wait_for_form_post_and_redirect(
    client: CDPClient,
    form_path: str,
    expected_path: str,
    before: dict[str, Any],
    label: str,
    result: VerificationResult,
    timeout: float = 10.0,
) -> None:
    """等待 AJAX 表单按既有 Form 协议完成整页 redirect。"""
    before_path, before_origin, _before_probe, _before_navigation_count = required_request_probe_identity(
        before,
        f"{label} form redirect before",
    )
    if before_path != form_path:
        raise VerificationError(f"{label} form redirect before path does not match form path")
    try:
        client.wait_for_load(timeout=timeout)
    except PageLoadTimeout:
        result.pageErrors.append(f"{label}: 表单提交后没有完成整页跳转")
        return
    client.pump(0.25)
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                "(() => ({ path: location.pathname, origin: location.origin, ready: document.readyState, omReady: document.documentElement?.dataset.omReady || '' }))()",
                timeout=5.0,
            ),
            f"{label} form redirect state",
        )
        last_state = state
        path = required_success_string(state, "path", f"{label} form redirect state")
        origin = required_success_string(state, "origin", f"{label} form redirect state")
        ready = required_success_string(state, "ready", f"{label} form redirect state")
        om_ready = required_string_field(state, "omReady", f"{label} form redirect state")
        if path == expected_path and origin == before_origin and ready == "complete" and om_ready == "true":
            return
        time.sleep(0.1)

    result.pageErrors.append(f"{label}: 表单提交跳转未通过，最后状态 {last_state}")


def find_table_edit_path(client: CDPClient, page_url: str, table_endpoint: str, row_text: str, href_prefix: str, label: str, result: VerificationResult) -> str:
    """打开筛选后的列表页，从真实表格行里提取编辑链接。"""
    navigate(client, page_url)
    wait_for_table_ready(client, table_endpoint, label, result)
    deadline = time.monotonic() + 10.0
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => {
  const rowText =
"""
            + json.dumps(row_text)
            + r""";
  const hrefPrefix =
"""
            + json.dumps(href_prefix)
            + r""";
  const rows = Array.from(document.querySelectorAll("[data-om-table-row]"));
  const row = rows.find((candidate) => candidate.textContent.includes(rowText));
  const link = row?.querySelector(`a[href^="${hrefPrefix}"][href$="/edit"]`);
  return {
    rowCount: rows.length,
    foundRow: Boolean(row),
    href: link ? new URL(link.getAttribute("href"), location.href).pathname : "",
    text: row?.textContent || ""
  };
})()
""",
                timeout=5.0,
            ),
            f"{label} table edit path state",
        )
        last_state = state
        row_count = required_non_negative_integer(state, "rowCount", f"{label} table edit path state")
        found_row = required_success_bool(state, "foundRow", f"{label} table edit path state")
        href = required_string_field(state, "href", f"{label} table edit path state")
        text = required_string_field(state, "text", f"{label} table edit path state")
        href_matches = re.fullmatch(re.escape(href_prefix) + r"[1-9][0-9]*/edit", href)
        if row_count > 0 and found_row and row_text in text and href_matches:
            return href
        time.sleep(0.1)
    result.pageErrors.append(f"{label}: 未找到临时记录编辑链接，最后状态 {last_state}")
    return ""


def assert_delete_form_redirects(client: CDPClient, base_url: str, edit_path: str, expected_path: str, label: str, result: VerificationResult) -> None:
    """在编辑页提交真实删除表单，并断言回到列表页。"""
    if not edit_path:
        return
    navigate(client, urllib.parse.urljoin(base_url, edit_path))
    assertion_result = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = Array.from(document.querySelectorAll("form[method='post']")).find((candidate) => candidate.action.endsWith("/delete"));
  if (!document.body.textContent.includes("Danger Zone")) failures.push("missing danger zone");
  if (!form) failures.push("missing delete form");
  if (!form?.querySelector("input[name='csrfmiddlewaretoken']")) failures.push("missing delete csrf token");
  return { failures };
})()
""",
        timeout=5.0,
    )
    assertion_result_failures = assertion_failures(assertion_result)
    result.pageErrors.extend(f"{label}: {failure}" for failure in assertion_result_failures)
    if assertion_result_failures:
        return
    client.load_seen = False
    client.evaluate(
        r"""
(() => {
  const form = Array.from(document.querySelectorAll("form[method='post']")).find((candidate) => candidate.action.endsWith("/delete"));
  form.submit();
  return true;
})()
""",
        timeout=5.0,
    )
    try:
        client.wait_for_load(timeout=8.0)
    except PageLoadTimeout:
        ready_state = client.evaluate("document.readyState", timeout=5.0)
        if ready_state != "complete":
            raise
    wait_for_path(client, expected_path, label, result)


def create_catalog_channel_gate_record(client: CDPClient, base_url: str, result: VerificationResult) -> tuple[str, str]:
    """通过真实表单创建临时目录频道，返回编辑路径和行文本。"""
    suffix = str(int(time.time() * 1000))
    channel_key = f"browser-gate-catalog-{suffix}"
    identity_name = f"Browser Gate Catalog {suffix}"
    navigate(client, urllib.parse.urljoin(base_url, "/catalog-channels/new"))
    submit_business_form(
        client,
        "/catalog-channels/new",
        {
            "channel_key": channel_key,
            "owner_country_code": "US",
            "channel_id": f"browser.gate.catalog.{suffix}",
            "identity_name": identity_name,
            "status": "provisional",
            "confidence": 72,
            "evidence_json": '{"source":"browser-gate","confidence":72}',
        },
        "/catalog-channels",
        "catalog channels create",
        result,
    )
    edit_path = find_table_edit_path(
        client,
        urllib.parse.urljoin(base_url, f"/catalog-channels?q={urllib.parse.quote(channel_key)}"),
        "/catalog-channels/table",
        channel_key,
        "/catalog-channels/",
        "catalog channels created row",
        result,
    )
    return edit_path, channel_key


def assert_catalog_channel_edit_and_delete(client: CDPClient, base_url: str, edit_path: str, result: VerificationResult) -> None:
    """验证临时目录频道编辑页结构和删除流程。"""
    if not edit_path:
        return
    navigate(client, urllib.parse.urljoin(base_url, edit_path))
    edit_assertion = client.evaluate(
        js_backend_page_assertions(
            edit_path,
            ["Edit Catalog Channel", "Channel Key", "Danger Zone", "Delete"],
            ["form[method='post']", "input[name='channel_key']", "textarea[name='evidence_json']", "button.om-button-danger, button.btn-danger"],
            "/catalog-channels",
        ),
        timeout=10.0,
    )
    edit_assertion_failures = assertion_failures(edit_assertion)
    result.pageErrors.extend(str(failure) for failure in edit_assertion_failures)
    assert_visual_health(client, "catalog channels edit", result)
    assert_delete_form_redirects(client, base_url, edit_path, "/catalog-channels", "catalog channels delete", result)


def assert_crud_create_edit_delete(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """通过浏览器创建临时频道/节目，验证编辑页和删除流程，再清理临时数据。"""
    suffix = str(int(time.time() * 1000))
    channel_name = f"Oldman Browser Gate {suffix}"
    channel_alias = f"Oldman Alias {suffix}"
    programme_title = f"Browser Gate Programme {suffix}"

    navigate(client, urllib.parse.urljoin(base_url, "/channels-epg/new"))
    submit_business_form(
        client,
        "/channels-epg/new",
        {
            "name": channel_name,
            "country": "US",
            "src_url": f"https://example.test/browser-gate/{suffix}.m3u8",
            "hits": 0,
            "last_date": "2026-06-10",
            "tvg_id_source": "manual",
            "tvg_id_confidence": 0,
            "description": "Temporary browser verification channel",
        },
        "/channels-epg",
        "channels create",
        result,
    )
    channel_edit_path = find_table_edit_path(
        client,
        urllib.parse.urljoin(base_url, f"/channels-epg?q={urllib.parse.quote(channel_name)}"),
        "/channels-epg/table",
        channel_name,
        "/channels-epg/",
        "channels created row",
        result,
    )
    channel_id = channel_edit_path.removeprefix("/channels-epg/").removesuffix("/edit") if channel_edit_path else ""
    if channel_edit_path:
        navigate(client, urllib.parse.urljoin(base_url, channel_edit_path))
        edit_assertion = client.evaluate(
            js_backend_page_assertions(
                channel_edit_path,
                ["Edit Channel", "Danger Zone", "Delete"],
                ["form[method='post']", "input[name='name']", "button.om-button-danger, button.btn-danger"],
                "/channels-epg",
            ),
            timeout=10.0,
        )
        edit_assertion_failures = assertion_failures(edit_assertion)
        result.pageErrors.extend(str(failure) for failure in edit_assertion_failures)
        assert_visual_health(client, "channels edit", result)

    channel_name_edit_path = ""
    if channel_id:
        navigate(client, urllib.parse.urljoin(base_url, "/channel-names/new"))
        submit_business_form(
            client,
            "/channel-names/new",
            {
                "name": channel_alias,
                "name_cn": f"中文 {suffix}",
                "name_tw": f"繁體 {suffix}",
                "name_es": f"Canal {suffix}",
                "logo": f"browser-gate-{suffix}.png",
                "logo_size": "12.50",
                "epg_id": channel_id,
                "epg_lookup": channel_name,
                "published": True,
                "channel_order": 1,
                "description": "Temporary browser verification channel name",
                "last_date": "2026-06-10",
            },
            "/channel-names",
            "channel names create",
            result,
        )
        channel_name_edit_path = find_table_edit_path(
            client,
            urllib.parse.urljoin(base_url, f"/channel-names?q={urllib.parse.quote(channel_alias)}"),
            "/channel-names/table",
            channel_alias,
            "/channel-names/",
            "channel names created row",
            result,
        )
        if channel_name_edit_path:
            navigate(client, urllib.parse.urljoin(base_url, channel_name_edit_path))
            channel_name_edit_assertion = client.evaluate(
                js_backend_page_assertions(
                    channel_name_edit_path,
                    ["Edit Channel Name", "Danger Zone", "Delete"],
                    ["form[method='post']", "select[name='epg_id']", "button.om-button-danger, button.btn-danger"],
                    "/channel-names",
                ),
                timeout=10.0,
            )
            channel_name_edit_assertion_failures = assertion_failures(channel_name_edit_assertion)
            result.pageErrors.extend(str(failure) for failure in channel_name_edit_assertion_failures)
            assert_visual_health(client, "channel names edit", result)
            assert_channel_name_epg_remote_fields(client, "channel names edit", result)

        navigate(client, urllib.parse.urljoin(base_url, "/epg-list/new"))
        submit_business_form(
            client,
            "/epg-list/new",
            {
                "channel_id": channel_id,
                "channel_lookup": channel_name,
                "title": programme_title,
                "start_date": "2026-06-10T12:30",
                "description": "Temporary browser verification programme",
            },
            "/epg-list",
            "programmes create",
            result,
        )
        programme_edit_path = find_table_edit_path(
            client,
            urllib.parse.urljoin(base_url, f"/epg-list?q={urllib.parse.quote(programme_title)}"),
            "/epg-list/table",
            programme_title,
            "/epg-list/",
            "programmes created row",
            result,
        )
        if programme_edit_path:
            navigate(client, urllib.parse.urljoin(base_url, programme_edit_path))
            programme_edit_assertion = client.evaluate(
                js_backend_page_assertions(
                    programme_edit_path,
                    ["Edit Programme", "Danger Zone", "Delete"],
                    ["form[method='post']", "select[name='channel_id']", "button.om-button-danger, button.btn-danger"],
                    "/epg-list",
                ),
                timeout=10.0,
            )
            programme_edit_assertion_failures = assertion_failures(programme_edit_assertion)
            result.pageErrors.extend(str(failure) for failure in programme_edit_assertion_failures)
            assert_visual_health(client, "programmes edit", result)
            assert_programme_datetime_picker(client, "programmes edit", result)
            assert_programme_channel_remote_fields(client, "programmes edit", result)
            assert_delete_form_redirects(client, base_url, programme_edit_path, "/epg-list", "programmes delete", result)

    assert_delete_form_redirects(client, base_url, channel_name_edit_path, "/channel-names", "channel names delete", result)
    assert_delete_form_redirects(client, base_url, channel_edit_path, "/channels-epg", "channels delete", result)


def assert_mobile_backend_pages(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """逐个后台页面在移动视口检查布局，并保存截图。"""
    mobile_pages = [
        ("/dashboard", "mobile-dashboard", result.mobileScreenshot, ["Dashboard"], [".page-content"]),
        ("/dashboard/analytics", "mobile-dashboard-analytics", "/tmp/oldman-mobile-dashboard-analytics.png", ["Analytics", "Programme Trend", "Feed Status", "Logo Quality"], ["#programme-trend-chart", "#feed-status-chart", "#logo-quality-chart", "[data-om-component='dashboard-overview']"]),
        ("/channels-epg", "mobile-channels-list", "/tmp/oldman-mobile-channels-list.png", ["Channels"], ["#channels-epg-table"]),
        ("/channel-names", "mobile-channel-names-list", "/tmp/oldman-mobile-channel-names-list.png", ["Channel Names"], ["#channel-names-table"]),
        ("/catalog-channels", "mobile-catalog-channels-list", "/tmp/oldman-mobile-catalog-channels-list.png", ["Catalog Channels"], ["#catalog-channels-table"]),
        ("/catalog-feeds", "mobile-catalog-feeds-list", "/tmp/oldman-mobile-catalog-feeds-list.png", ["Catalog Feeds"], ["#catalog-feeds-table"]),
        ("/upstream-records", "mobile-upstream-records-list", "/tmp/oldman-mobile-upstream-records-list.png", ["Upstream Records"], ["#upstream-records-table", "[data-om-component='list']"]),
        ("/logo-assets", "mobile-logo-assets-list", "/tmp/oldman-mobile-logo-assets-list.png", ["Logo Assets"], ["#logo-assets-table", "[data-om-component='slider']", "[data-om-component='apex-chart']"]),
        ("/match-decisions", "mobile-match-decisions-list", "/tmp/oldman-mobile-match-decisions-list.png", ["Match Decisions"], ["#match-decisions-table", "#match-decision-edit-modal"]),
        ("/users", "mobile-users-list", "/tmp/oldman-mobile-users-list.png", ["Users"], ["#users-table", "#user-password-modal"]),
        ("/user-session", "mobile-user-session", "/tmp/oldman-mobile-user-session.png", ["User Session"], ["#user-session-feedback", "#user-session-password-modal", "#user-session-permissions-modal"]),
        ("/notifications", "mobile-notifications-list", "/tmp/oldman-mobile-notifications-list.png", ["Notifications"], ["#notifications-table", "#notification-detail-modal"]),
        ("/epg-list", "mobile-programmes-list", "/tmp/oldman-mobile-programmes-list.png", ["Programmes"], ["#epg-list-table"]),
        ("/channels-epg/new", "mobile-channel-form", "/tmp/oldman-mobile-channel-form.png", ["New Channel", "Save"], ["form[data-om-form]"]),
        ("/channel-names/new", "mobile-channel-name-form", "/tmp/oldman-mobile-channel-name-form.png", ["New Channel Name", "Save"], ["form[data-om-form]"]),
        ("/catalog-channels/new", "mobile-catalog-channel-form", "/tmp/oldman-mobile-catalog-channel-form.png", ["New Catalog Channel", "Save"], ["form[data-om-form]"]),
        ("/catalog-feeds/new", "mobile-catalog-feed-form", "/tmp/oldman-mobile-catalog-feed-form.png", ["New Catalog Feed", "Save"], ["form[data-om-form]", "[data-om-component='upload']"]),
        ("/epg-list/new", "mobile-programme-form", "/tmp/oldman-mobile-programme-form.png", ["New Programme", "Save"], ["form[data-om-form]"]),
    ]
    configure_viewport(client, 390, 844, mobile=True)
    nav_active_paths = {
        "/dashboard",
        "/dashboard/analytics",
        "/channels-epg",
        "/channel-names",
        "/catalog-channels",
        "/catalog-feeds",
        "/upstream-records",
        "/logo-assets",
        "/match-decisions",
        "/users",
        "/user-session",
        "/notifications",
        "/epg-list",
    }
    for path, name, screenshot_path, texts, selectors in mobile_pages:
        navigate(client, urllib.parse.urljoin(base_url, path))
        assertion_result = client.evaluate(js_backend_page_assertions(path, texts, selectors, path if path in nav_active_paths else ""), timeout=10.0)
        assertion_result_failures = assertion_failures(assertion_result)
        result.pageErrors.extend(str(failure) for failure in assertion_result_failures)
        assert_visual_health(client, name, result, mobile=True)
        if any("table" in selector for selector in selectors):
            mobile_table_result = client.evaluate(
                r"""
(() => {
  const failures = [];
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return !element.hidden && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const tables = Array.from(document.querySelectorAll(".om-table"))
    .filter(visible)
    .filter((table) => table.querySelector("thead"));
  for (const table of tables.slice(0, 8)) {
    const scroll = table.closest(".om-table-scroll");
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    const firstRow = table.querySelector("tbody tr");
    const firstCell = table.querySelector("tbody td");
    const actionCell = table.querySelector('tbody td[data-om-column="action"]');
    const tableStyle = getComputedStyle(table);
    const scrollStyle = scroll ? getComputedStyle(scroll) : null;
    if (tableStyle.display === "block") failures.push("mobile table is still rendered as card/block layout");
    if (!visible(thead)) failures.push("mobile table header is hidden; expected horizontal table layout");
    if (tbody && getComputedStyle(tbody).display === "flex") failures.push("mobile table body is still flex card layout");
    if (firstRow && getComputedStyle(firstRow).display === "block") failures.push("mobile table rows are still block cards");
    if (firstCell && getComputedStyle(firstCell).display.includes("grid")) failures.push("mobile table cells are still grid label/value cards");
    if (firstCell && getComputedStyle(firstCell, "::before").content !== "none") failures.push("mobile table cells still render pseudo labels");
    if (scroll && scrollStyle && !["auto", "scroll"].includes(scrollStyle.overflowX)) failures.push("mobile table scroll container does not allow horizontal scrolling");
    if (scroll && table.getBoundingClientRect().width <= scroll.getBoundingClientRect().width + 8) {
      failures.push("mobile table does not preserve a wider scrollable table width");
    }
    if (actionCell && getComputedStyle(actionCell).position !== "sticky") failures.push("mobile table action column is not sticky");
  }
  return { failures };
})()
""",
                timeout=5.0,
            )
            mobile_table_result_failures = assertion_failures(mobile_table_result)
            result.pageErrors.extend(f"{name}: {failure}" for failure in mobile_table_result_failures)
        if path == "/dashboard":
            mobile_assertion_result = client.evaluate(js_mobile_assertions(), timeout=10.0)
            mobile_assertion_result_failures = assertion_failures(mobile_assertion_result)
            result.pageErrors.extend(str(failure) for failure in mobile_assertion_result_failures)
        if path == "/epg-list/new":
            assert_programme_datetime_picker(client, name, result)
        capture_named_screenshot(client, result, name, screenshot_path)


async def ensure_match_decision_gate_record() -> None:
    """为浏览器门禁准备真实人工决策数据，避免空库时跳过关键交互。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from apps.epg_admin.models import CatalogChannel, CatalogFeed, CatalogMatchDecision, UpstreamSourceRecord
    from apps.epg_admin.tables import MatchDecisionTable
    from sqlalchemy import func, select

    from oldman.db import db_manager

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    async with db_manager.get_session() as session:
        count_row = (
            await session.execute(
                select(func.count(CatalogMatchDecision.id))
                .join(CatalogChannel, CatalogMatchDecision.catalog_channel_id == CatalogChannel.id)
                .join(CatalogFeed, CatalogMatchDecision.catalog_feed_id == CatalogFeed.id)
                .join(UpstreamSourceRecord, CatalogMatchDecision.source_record_id == UpstreamSourceRecord.id)
                .where(
                    CatalogMatchDecision.decided_by == "browser-gate",
                    CatalogChannel.channel_key.like("browser-gate-match-%"),
                    CatalogFeed.canonical_name.like("Browser Gate Match Feed%"),
                    UpstreamSourceRecord.primary_name.like("Browser Gate Match Source%"),
                )
            )
        ).one()
        browser_gate_match_count = int(count_row[0] or 0)
        required_count = MatchDecisionTable.page_size_options[0] + 1
        for index in range(max(0, required_count - browser_gate_match_count)):
            suffix = f"{int(time.time() * 1000)}-{index}"
            catalog_channel = CatalogChannel(
                channel_key=f"browser-gate-match-{suffix}",
                owner_country_code="US",
                channel_id=f"browser.gate.match.{suffix}",
                identity_name=f"Browser Gate Match {suffix}",
                status="provisional",
                confidence=80,
                evidence_json='{"source":"browser-gate"}',
                created_at=now,
                updated_at=now,
            )
            session.add(catalog_channel)
            await session.flush()

            catalog_feed = CatalogFeed(
                catalog_channel_id=int(catalog_channel.id),
                feed_suffix=f"gate-{suffix}",
                tvg_id=f"browser.gate.match.feed.{suffix}",
                is_default=True,
                canonical_name=f"Browser Gate Match Feed {suffix}",
                accepted_names_text=f"Browser Gate Match Feed {suffix}",
                compatible_tvg_ids_text=f"browser.gate.match.feed.{suffix}",
                service_country_code="US",
                region_code="US-CA",
                language_hints_text="en",
                timezone_hints_text="America/Los_Angeles",
                version_kind="default",
                status="provisional",
                confidence=80,
                evidence_json='{"source":"browser-gate-feed"}',
                created_at=now,
                updated_at=now,
            )
            session.add(catalog_feed)
            await session.flush()

            source_record = UpstreamSourceRecord(
                catalog_feed_id=int(catalog_feed.id),
                source_code="browser-gate",
                source_record_key=f"match-source-{suffix}",
                record_kind="channel",
                source_url="https://example.test/browser-gate",
                primary_name=f"Browser Gate Match Source {suffix}",
                logo_urls_text="",
                raw_payload='{"source":"browser-gate-match"}',
                raw_hash=f"matchhash{suffix}",
                status="active",
                first_seen_at=now,
                last_seen_at=now,
                disappeared_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(source_record)
            await session.flush()

            session.add(
                CatalogMatchDecision(
                    decision_scope="feed",
                    source_record_id=int(source_record.id),
                    catalog_channel_id=int(catalog_channel.id),
                    catalog_feed_id=int(catalog_feed.id),
                    decision="accepted",
                    reason=f"Browser gate seed reason {suffix}",
                    evidence_json='{"score":0.92,"source":"browser-gate-match"}',
                    decided_by="browser-gate",
                    created_at=now,
                )
            )


async def ensure_epg_gate_records() -> None:
    """为 oldman_dev 空库准备频道、频道别名和节目单门禁数据。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from apps.epg_admin.models import ChannelName, ChannelsEpg, EpgList
    from apps.epg_admin.tables import ChannelsEpgTable
    from sqlalchemy import select

    from oldman.db import db_manager

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    today = dt.date(2026, 6, 10)
    async with db_manager.get_session() as session:
        required_count = ChannelsEpgTable.page_size_options[0] + 1
        for index in range(1, required_count + 1):
            channel_name_value = f"Browser Gate Channel Seed {index:02d}"
            channel = (
                await session.execute(select(ChannelsEpg).where(ChannelsEpg.name == channel_name_value))
            ).scalars().one_or_none()
            if channel is None:
                channel = ChannelsEpg(
                    name=channel_name_value,
                    src_url=f"https://example.test/browser-gate/channel-{index:02d}.m3u8",
                    icon=f"browser-gate-channel-{index:02d}.png",
                    country="US",
                    description="Browser gate seed channel for oldman_dev.",
                    hits=index,
                    last_date=today,
                    create_date=now - dt.timedelta(minutes=index),
                    tvg_id=f"browser.gate.channel.{index:02d}",
                    tvg_id_lookup=f"browser.gate.channel.{index:02d}",
                    tvg_id_source="browser-gate",
                    tvg_id_confidence=90,
                    tvg_id_locked=False,
                    tvg_id_resolved_at=now,
                    tvg_id_reason="Browser gate seed",
                )
                session.add(channel)
                await session.flush()

            alias_name = f"Browser Gate Alias Seed {index:02d}"
            alias = (
                await session.execute(select(ChannelName).where(ChannelName.name == alias_name))
            ).scalars().one_or_none()
            if alias is None:
                alias = ChannelName(
                    name=alias_name,
                    name_cn=f"门禁频道 {index:02d}",
                    name_tw=f"門禁頻道 {index:02d}",
                    name_es=f"Canal Gate {index:02d}",
                    logo=f"browser-gate-alias-{index:02d}.png",
                    logo_size=Decimal("12.50"),
                    epg_id=int(channel.id),
                    published=True,
                    channel_order=index,
                    category_id=1,
                    country_id=1,
                    language_id=1,
                    description="Browser gate seed channel name for oldman_dev.",
                    last_date=today,
                    create_date=now - dt.timedelta(minutes=index),
                )
                session.add(alias)
            else:
                alias.epg_id = int(channel.id)
                alias.published = True
                alias.channel_order = index

            programme_title = f"Browser Gate Programme Seed {index:02d}"
            programme = (
                await session.execute(select(EpgList).where(EpgList.title == programme_title))
            ).scalars().one_or_none()
            if programme is None:
                session.add(
                    EpgList(
                        channel_id=int(channel.id),
                        title=programme_title,
                        start_date=now + dt.timedelta(hours=index),
                        description="Browser gate seed programme for oldman_dev.",
                        create_date=now - dt.timedelta(minutes=index),
                    )
                )
            else:
                programme.channel_id = int(channel.id)


async def ensure_logo_asset_gate_records() -> None:
    """为 Logo Assets 页面和图表准备 feed 级 logo 门禁数据。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from apps.epg_admin.models import CatalogFeed, CatalogLogoAsset
    from apps.epg_admin.tables import LogoAssetTable
    from sqlalchemy import select

    from oldman.db import db_manager

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    async with db_manager.get_session() as session:
        required_count = LogoAssetTable.page_size_options[0] + 1
        feeds = (
            (
                await session.execute(
                    select(CatalogFeed)
                    .where(CatalogFeed.canonical_name.like("Browser Gate Match Feed%"))
                    .order_by(CatalogFeed.id.asc())
                    .limit(required_count)
                )
            )
            .scalars()
            .all()
        )
        for index, feed in enumerate(feeds, start=1):
            existing = (
                await session.execute(
                    select(CatalogLogoAsset).where(CatalogLogoAsset.catalog_feed_id == int(feed.id))
                )
            ).scalars().one_or_none()
            if existing is not None:
                existing.quality_score = 55 + index * 10
                existing.updated_at = now
                continue

            session.add(
                CatalogLogoAsset(
                    catalog_feed_id=int(feed.id),
                    source_kind="browser-gate",
                    source_url=f"https://example.test/browser-gate/logo-{index:02d}.png",
                    original_url=f"https://example.test/browser-gate/logo-{index:02d}.png",
                    original_path=f"browser-gate/original-{index:02d}.png",
                    normalized_path=f"browser-gate/normalized-{index:02d}.png",
                    preview_path=f"browser-gate/preview-{index:02d}.png",
                    edge_map_path=f"browser-gate/edge-{index:02d}.png",
                    mask_map_path=f"browser-gate/mask-{index:02d}.png",
                    width=128 + index,
                    height=72 + index,
                    mime_type="image/png",
                    sha256=f"browsergatelogosha{index:02d}".ljust(64, "0")[:64],
                    phash=f"phash{index:02d}",
                    dhash=f"dhash{index:02d}",
                    edge_phash=f"edge{index:02d}",
                    mask_phash=f"mask{index:02d}",
                    visible_bbox_json='{"x":0,"y":0,"w":128,"h":72}',
                    quality_score=55 + index * 10,
                    created_at=now - dt.timedelta(minutes=index),
                    updated_at=now - dt.timedelta(minutes=index),
                )
            )


async def ensure_user_gate_records() -> None:
    """为 Users 浏览器门禁准备普通用户和非当前超级用户。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from apps.auth.models import OldmanUser
    from sqlalchemy import select

    from oldman.db import db_manager

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    async with db_manager.get_session() as session:
        normal_result = await session.execute(select(OldmanUser).where(OldmanUser.username == "browser_gate_user"))
        normal_user = normal_result.scalars().one_or_none()
        if normal_user is None:
            normal_user = OldmanUser(
                username="browser_gate_user",
                email="browser-gate-user@example.test",
                display_name="Browser Gate User",
                is_active=True,
                is_staff=True,
                is_superuser=False,
                password_hash="",
                last_login_at=now,
            )
            session.add(normal_user)
        normal_user.email = "browser-gate-user@example.test"
        normal_user.display_name = "Browser Gate User"
        normal_user.is_active = True
        normal_user.is_staff = True
        normal_user.is_superuser = False
        normal_user.last_login_at = normal_user.last_login_at or now
        normal_user.set_password("UserGateOldPass!2026")

        super_result = await session.execute(select(OldmanUser).where(OldmanUser.username == "browser_gate_superuser"))
        super_user = super_result.scalars().one_or_none()
        if super_user is None:
            super_user = OldmanUser(
                username="browser_gate_superuser",
                email="browser-gate-superuser@example.test",
                display_name="Browser Gate Superuser",
                is_active=True,
                is_staff=True,
                is_superuser=True,
                password_hash="",
                last_login_at=now,
            )
            session.add(super_user)
        super_user.email = "browser-gate-superuser@example.test"
        super_user.display_name = "Browser Gate Superuser"
        super_user.is_active = True
        super_user.is_staff = True
        super_user.is_superuser = True
        super_user.last_login_at = super_user.last_login_at or now
        super_user.set_password("SuperGatePass!2026")

        for index in range(1, 13):
            username = f"browser_gate_page_user_{index:02d}"
            page_result = await session.execute(select(OldmanUser).where(OldmanUser.username == username))
            page_user = page_result.scalars().one_or_none()
            if page_user is None:
                page_user = OldmanUser(
                    username=username,
                    email=f"{username}@example.test",
                    display_name=f"Browser Gate Page User {index:02d}",
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                    password_hash="",
                    last_login_at=now - dt.timedelta(minutes=index),
                )
                session.add(page_user)
            page_user.email = f"{username}@example.test"
            page_user.display_name = f"Browser Gate Page User {index:02d}"
            page_user.is_active = True
            page_user.is_staff = False
            page_user.is_superuser = False
            page_user.last_login_at = page_user.last_login_at or now - dt.timedelta(minutes=index)
            page_user.set_password("PageUserPass!2026")


async def ensure_dashboard_browser_gate_records() -> None:
    """在已迁移的隔离数据库中准备浏览器门禁种子数据。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from oldman import bootstrap_service

    config_file = os.environ.get("OLDMAN_GATE_CONFIG_FILE")
    if not config_file:
        raise RuntimeError("OLDMAN_GATE_CONFIG_FILE is required for browser fixtures")
    context = bootstrap_service("web", config_file=config_file)

    from oldman.db import db_manager

    if not context.settings.database.url:
        raise RuntimeError("settings.database.url is required before preparing browser gate records")

    try:
        await ensure_epg_gate_records()
        await ensure_match_decision_gate_record()
        await ensure_notification_gate_record()
        await ensure_logo_asset_gate_records()
        await ensure_user_gate_records()
    finally:
        await db_manager.close()


async def ensure_notification_gate_record() -> None:
    """为通知中心准备稳定的人工审核通知数据。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from apps.epg_admin.models import CatalogChannel, CatalogFeed, CatalogMatchDecision, UpstreamSourceRecord
    from sqlalchemy import select

    from oldman.db import db_manager

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    async with db_manager.get_session() as session:
        existing = (
            await session.execute(
                select(CatalogMatchDecision).where(
                    CatalogMatchDecision.decision == "manual_review",
                    CatalogMatchDecision.reason.like("Browser Gate Notification%"),
                )
            )
        ).scalars().first()
        if existing is not None:
            existing.decision_scope = "feed"
            existing.decision = "manual_review"
            existing.reason = "Browser Gate Notification review gate"
            existing.decided_by = "browser-gate"
            existing.created_at = now
            return

        suffix = str(int(time.time() * 1000))
        catalog_channel = CatalogChannel(
            channel_key=f"browser-gate-notification-{suffix}",
            owner_country_code="US",
            channel_id=f"browser.gate.notification.{suffix}",
            identity_name=f"Browser Gate Notification {suffix}",
            status="provisional",
            confidence=60,
            evidence_json='{"source":"browser-gate-notification"}',
            created_at=now,
            updated_at=now,
        )
        session.add(catalog_channel)
        await session.flush()

        catalog_feed = CatalogFeed(
            catalog_channel_id=int(catalog_channel.id),
            feed_suffix=f"notification-{suffix}",
            tvg_id=f"browser.gate.notification.feed.{suffix}",
            is_default=True,
            canonical_name=f"Browser Gate Notification Feed {suffix}",
            accepted_names_text=f"Browser Gate Notification Feed {suffix}",
            compatible_tvg_ids_text=f"browser.gate.notification.feed.{suffix}",
            service_country_code="US",
            region_code="US-CA",
            language_hints_text="en",
            timezone_hints_text="America/Los_Angeles",
            version_kind="default",
            status="provisional",
            confidence=60,
            evidence_json='{"source":"browser-gate-notification-feed"}',
            created_at=now,
            updated_at=now,
        )
        session.add(catalog_feed)
        await session.flush()

        source_record = UpstreamSourceRecord(
            catalog_feed_id=int(catalog_feed.id),
            source_code="browser-gate",
            source_record_key=f"notification-source-{suffix}",
            record_kind="channel",
            source_url="https://example.test/browser-gate-notification",
            primary_name=f"Browser Gate Notification Source {suffix}",
            logo_urls_text="",
            raw_payload='{"source":"browser-gate-notification"}',
            raw_hash=f"notifhash{suffix}",
            status="active",
            first_seen_at=now,
            last_seen_at=now,
            disappeared_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(source_record)
        await session.flush()

        session.add(
            CatalogMatchDecision(
                decision_scope="feed",
                source_record_id=int(source_record.id),
                catalog_channel_id=int(catalog_channel.id),
                catalog_feed_id=int(catalog_feed.id),
                decision="manual_review",
                reason=f"Browser Gate Notification review {suffix}",
                evidence_json='{"source":"browser-gate-notification","requires":"manual_review"}',
                decided_by="browser-gate",
                created_at=now,
            )
        )


def assert_logout_flow(client: CDPClient, base_url: str, result: VerificationResult) -> None:
    """验证顶栏用户菜单里的退出登录能回到登录页。"""
    configure_viewport(client, 1440, 1000, mobile=False)
    navigate(client, urllib.parse.urljoin(base_url, "/user-session"))
    logout_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const userToggle = document.querySelector("#page-header-user-dropdown");
  if (!userToggle) return { failures: ["missing user dropdown toggle"] };
  userToggle.click();
  await sleep(100);
  const logout = userToggle.closest("[data-om-component='dropdown'], .oldman-dropdown, .dropdown")?.querySelector('a[href="/logout"]');
  if (!logout) return { failures: ["missing logout link"] };
  window.location.href = logout.href;
  return { failures: [], href: logout.href };
})()
""",
        timeout=5.0,
    )
    logout_result_failures = assertion_failures(logout_result)
    result.pageErrors.extend(f"logout: {failure}" for failure in logout_result_failures)
    if logout_result_failures:
        return
    logout_payload = required_payload_object(logout_result, "logout action")
    logout_href = required_success_string(logout_payload, "href", "logout action")
    logout_url = urllib.parse.urlparse(logout_href)
    expected_logout_url = urllib.parse.urlparse(urllib.parse.urljoin(base_url, "/logout"))
    if (
        logout_url.scheme.lower(),
        logout_url.netloc.lower(),
        logout_url.path,
        logout_url.params,
        logout_url.query,
        logout_url.fragment,
    ) != (
        expected_logout_url.scheme.lower(),
        expected_logout_url.netloc.lower(),
        expected_logout_url.path,
        "",
        "",
        "",
    ):
        raise VerificationError("logout action payload href must target the same-origin /logout route")
    deadline = time.monotonic() + 10.0
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = required_payload_object(
            client.evaluate(
                r"""
(() => ({
  path: location.pathname,
  ready: document.readyState,
  hasLoginForm: Boolean(document.querySelector('form[action="/login"]')),
  hasUsername: Boolean(document.querySelector('input[name="username"]')),
  hasPassword: Boolean(document.querySelector('input[name="password"]'))
}))()
""",
                timeout=5.0,
            ),
            "logout state",
        )
        last_state = state
        path = required_string_field(state, "path", "logout state")
        ready = required_string_field(state, "ready", "logout state")
        has_login_form = required_success_bool(state, "hasLoginForm", "logout state")
        has_username = required_success_bool(state, "hasUsername", "logout state")
        has_password = required_success_bool(state, "hasPassword", "logout state")
        if path == "/login" and ready == "complete" and has_login_form and has_username and has_password:
            return
        time.sleep(0.1)
    result.pageErrors.append(f"logout: 退出登录后未回到登录页，状态 {last_state}")


def verify_dashboard(url: str, result: VerificationResult) -> None:
    """对真实后台首页执行完整浏览器验证。"""
    port = find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="oldman-chrome-")
    chrome = launch_chrome(port, user_data_dir)
    client: CDPClient | None = None
    try:
        wait_for_chrome_devtools(chrome, port)
        page_ws = create_page_websocket(port)
        client = CDPClient(page_ws, result)
        client.command("Page.enable")
        client.command("Runtime.enable")
        client.command("Network.enable")
        client.command("Log.enable")

        configure_viewport(client, 1440, 1000, mobile=False)
        assert_preloader_critical_first_paint(client, url, result)
        asyncio.run(ensure_dashboard_browser_gate_records())
        login(
            client,
            url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )
        navigate(client, url)
        assertion_result = client.evaluate(js_assertions(), timeout=10.0)
        failures = assertion_failures(assertion_result)
        result.pageErrors.extend(str(failure) for failure in failures)
        save_screenshot(client, result.desktopScreenshot)
        result.screenshots["desktop-dashboard"] = result.desktopScreenshot
        assert_native_datetime_local_control(client, result)
        assert_business_shell_interactions(client, result)
        assert_back_to_top_interaction(client, result)
        assert_backend_shell_frame_navigation(client, result)
        navigate(client, url)
        assert_dashboard_chart_loading_overlay_3g(port, url, result)
        navigate(client, url)
        assert_dashboard_programme_chart(client, result)
        assert_dashboard_overview_interactions(client, result)
        assert_backend_page(
            client,
            url,
            "/dashboard/analytics",
            ["Analytics", "Programme Trend", "Feed Status", "Logo Quality"],
            ["#programme-trend-chart", "#feed-status-chart", "#logo-quality-chart", "[data-om-component='dashboard-overview']"],
            "/dashboard/analytics",
            result,
        )
        assert_dashboard_programme_chart(client, result)
        assert_dashboard_overview_interactions(client, result)
        capture_named_screenshot(client, result, "desktop-dashboard-analytics", "/tmp/oldman-desktop-dashboard-analytics.png")
        click_and_assert_turbo(client, 'a[href="/channels-epg"]', "/channels-epg", "sidebar channels", result)
        assert_backend_page(client, url, "/channels-epg", ["Channels", "New Channel", "Search"], ['a[href="/channels-epg/new"]'], "/channels-epg", result)
        capture_named_screenshot(client, result, "desktop-channels-list", "/tmp/oldman-desktop-channels-list.png")
        assert_list_interactions(client, "/channels-epg", "/channels-epg", "/channels-epg/table", result)
        click_and_assert_turbo(client, 'a[href="/channels-epg/new"]', "/channels-epg/new", "channels new", result)
        form_assertion = client.evaluate(
            js_backend_page_assertions(
                "/channels-epg/new",
                ["New Channel", "Name", "Source URL", "Save"],
                ["form[method='post']", "input[name='csrfmiddlewaretoken']", "input[name='src_url']"],
                "/channels-epg",
            ),
            timeout=10.0,
        )
        form_assertion_failures = assertion_failures(form_assertion)
        result.pageErrors.extend(str(failure) for failure in form_assertion_failures)
        assert_visual_health(client, "channels new", result)
        capture_named_screenshot(client, result, "desktop-channel-form", "/tmp/oldman-desktop-channel-form.png")
        assert_form_ajax_validation(client, "/channels-epg/new", "name", result)
        navigate(client, urllib.parse.urljoin(url, "/channels-epg/new"))
        assert_form_html_fragment_validation(client, "/channels-epg/new", "name", result)

        click_and_assert_turbo(client, 'a[href="/channel-names"]', "/channel-names", "sidebar channel names", result)
        assert_backend_page(client, url, "/channel-names", ["Channel Names", "New Channel Name", "Filter"], ['a[href="/channel-names/new"]'], "/channel-names", result)
        capture_named_screenshot(client, result, "desktop-channel-names-list", "/tmp/oldman-desktop-channel-names-list.png")
        assert_list_interactions(client, "/channel-names", "/channel-names", "/channel-names/table", result)
        assert_channel_names_filter_interactions(client, result)
        click_and_assert_turbo(client, 'a[href="/channel-names/new"]', "/channel-names/new", "channel names new", result)
        channel_name_form_assertion = client.evaluate(
            js_backend_page_assertions(
                "/channel-names/new",
                ["New Channel Name", "Name", "Logo", "Save"],
                ["form[method='post']", "input[name='csrfmiddlewaretoken']", "select[name='epg_id']"],
                "/channel-names",
            ),
            timeout=10.0,
        )
        channel_name_form_assertion_failures = assertion_failures(channel_name_form_assertion)
        result.pageErrors.extend(str(failure) for failure in channel_name_form_assertion_failures)
        assert_visual_health(client, "channel names new", result)
        capture_named_screenshot(client, result, "desktop-channel-name-form", "/tmp/oldman-desktop-channel-name-form.png")
        assert_remote_select_loaded(client, "/admin/select/channels", "select[name='epg_id']", "channel name EPG select", result)
        assert_remote_select_search(client, "/admin/select/channels", "select[name='epg_id']", "a", "channel name EPG select search", result)
        assert_remote_autocomplete_search(
            client,
            "/admin/select/channels",
            "[data-om-component='autocomplete'] [data-om-autocomplete-input]",
            "a",
            "channel name EPG autocomplete",
            result,
        )
        assert_form_ajax_validation(client, "/channel-names/new", "name", result)

        click_and_assert_turbo(client, 'a[href="/catalog-channels"]', "/catalog-channels", "sidebar catalog channels", result)
        assert_backend_page(
            client,
            url,
            "/catalog-channels",
            ["Catalog Channels", "New Catalog Channel", "Filter"],
            ['a[href="/catalog-channels/new"]', "[data-om-component='slider']"],
            "/catalog-channels",
            result,
        )
        click_and_assert_turbo(client, 'a[href="/catalog-channels/new"]', "/catalog-channels/new", "catalog channels new", result)
        catalog_form_assertion = client.evaluate(
            js_backend_page_assertions(
                "/catalog-channels/new",
                ["New Catalog Channel", "Channel Key", "Identity Name", "Save"],
                ["form[method='post']", "input[name='channel_key']", "textarea[name='evidence_json']"],
                "/catalog-channels",
            ),
            timeout=10.0,
        )
        catalog_form_assertion_failures = assertion_failures(catalog_form_assertion)
        result.pageErrors.extend(str(failure) for failure in catalog_form_assertion_failures)
        assert_visual_health(client, "catalog channels new", result)
        capture_named_screenshot(client, result, "desktop-catalog-channel-form", "/tmp/oldman-desktop-catalog-channel-form.png")
        assert_form_ajax_validation(client, "/catalog-channels/new", "channel_key", result)
        catalog_edit_path, _catalog_channel_key = create_catalog_channel_gate_record(client, url, result)
        navigate(client, urllib.parse.urljoin(url, "/catalog-channels"))
        capture_named_screenshot(client, result, "desktop-catalog-channels-list", "/tmp/oldman-desktop-catalog-channels-list.png")
        assert_list_interactions(client, "/catalog-channels", "/catalog-channels", "/catalog-channels/table", result)
        assert_catalog_channel_edit_cancel_returns_to_list(client, url, result)
        navigate(client, urllib.parse.urljoin(url, "/catalog-channels"))
        wait_for_table_ready(client, "/catalog-channels/table", "catalog channels filter reset", result)
        assert_catalog_channels_filter_interactions(client, result)
        navigate(client, urllib.parse.urljoin(url, "/catalog-channels"))
        wait_for_table_ready(client, "/catalog-channels/table", "catalog channels evidence reset", result)
        assert_catalog_channel_evidence_modal(client, result)

        catalog_channel_id = catalog_edit_path.removeprefix("/catalog-channels/").removesuffix("/edit") if catalog_edit_path else ""
        click_and_assert_turbo(client, 'a[href="/catalog-feeds"]', "/catalog-feeds", "sidebar catalog feeds", result)
        assert_backend_page(
            client,
            url,
            "/catalog-feeds",
            ["Catalog Feeds", "New Catalog Feed", "Filter"],
            ['a[href="/catalog-feeds/new"]', "#catalog-feeds-table"],
            "/catalog-feeds",
            result,
        )
        click_and_assert_turbo(client, 'a[href="/catalog-feeds/new"]', "/catalog-feeds/new", "catalog feeds new", result)
        feed_form_assertion = client.evaluate(
            js_backend_page_assertions(
                "/catalog-feeds/new",
                ["New Catalog Feed", "Catalog Channel", "TVG ID", "Logo Upload Preview", "Save"],
                ["form[method='post']", "select[name='catalog_channel_id']", "[data-om-component='upload']"],
                "/catalog-feeds",
            ),
            timeout=10.0,
        )
        feed_form_assertion_failures = assertion_failures(feed_form_assertion)
        result.pageErrors.extend(str(failure) for failure in feed_form_assertion_failures)
        assert_visual_health(client, "catalog feeds new", result)
        capture_named_screenshot(client, result, "desktop-catalog-feed-form", "/tmp/oldman-desktop-catalog-feed-form.png")
        assert_remote_select_loaded(client, "/admin/select/catalog_channels", "select[name='catalog_channel_id']", "catalog feed CatalogChannel select", result)
        assert_remote_select_search(client, "/admin/select/catalog_channels", "select[name='catalog_channel_id']", "Browser", "catalog feed CatalogChannel select search", result)
        assert_remote_select_pagination(client, "/admin/select/catalog_channels", "select[name='catalog_channel_id']", "catalog feed CatalogChannel select pagination", result)
        assert_catalog_feed_upload_preview(client, "catalog feeds new", result)
        assert_form_ajax_validation(client, "/catalog-feeds/new", "tvg_id", result)
        catalog_feed_edit_path = ""
        if catalog_channel_id:
            catalog_feed_edit_path, _catalog_feed_tvg_id = create_catalog_feed_gate_record(client, url, catalog_channel_id, result)
            navigate(client, urllib.parse.urljoin(url, "/catalog-feeds"))
            capture_named_screenshot(client, result, "desktop-catalog-feeds-list", "/tmp/oldman-desktop-catalog-feeds-list.png")
            assert_list_interactions(client, "/catalog-feeds", "/catalog-feeds", "/catalog-feeds/table", result)
            assert_catalog_feed_filter_interactions(client, result)
            assert_catalog_feed_edit_and_delete(client, url, catalog_feed_edit_path, result)
        assert_catalog_channel_edit_and_delete(client, url, catalog_edit_path, result)

        navigate(client, urllib.parse.urljoin(url, "/upstream-records"))
        assert_backend_page(
            client,
            url,
            "/upstream-records",
            ["Upstream Records", "Filter", "Sources & Status"],
            ["#upstream-records-table", "[data-om-component='list']", "[data-om-component='modal']"],
            "/upstream-records",
            result,
        )
        capture_named_screenshot(client, result, "desktop-upstream-records-list", "/tmp/oldman-desktop-upstream-records-list.png")
        assert_list_interactions(client, "/upstream-records", "/upstream-records", "/upstream-records/table", result)
        assert_upstream_record_filter_interactions(client, result)
        assert_upstream_catalog_feed_autocomplete_filter(client, result)
        assert_upstream_record_local_list(client, result)
        navigate(client, urllib.parse.urljoin(url, "/upstream-records"))
        wait_for_table_ready(client, "/upstream-records/table", "upstream records raw modal", result)
        assert_upstream_record_raw_modal(client, result)

        login(
            client,
            url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )
        navigate(client, urllib.parse.urljoin(url, "/logo-assets"))
        assert_backend_page(
            client,
            url,
            "/logo-assets",
            ["Logo Assets", "Filter", "Quality Distribution", "MIME Distribution", "Dimensions"],
            ["#logo-assets-table", "[data-om-component='slider']", "[data-om-component='apex-chart']", "[data-om-component='modal']"],
            "/logo-assets",
            result,
        )
        capture_named_screenshot(client, result, "desktop-logo-assets-list", "/tmp/oldman-desktop-logo-assets-list.png")
        assert_logo_asset_charts(client, result)
        assert_list_interactions(client, "/logo-assets", "/logo-assets", "/logo-assets/table", result)
        assert_logo_asset_filter_interactions(client, result)
        assert_logo_catalog_feed_autocomplete_filter(client, result)
        navigate(client, urllib.parse.urljoin(url, "/logo-assets"))
        wait_for_table_ready(client, "/logo-assets/table", "logo assets compare modal", result)
        assert_logo_asset_thumbnail_column(client, result)
        assert_logo_asset_compare_modal(client, result)

        navigate(client, urllib.parse.urljoin(url, "/match-decisions"))
        assert_backend_page(
            client,
            url,
            "/match-decisions",
            ["Match Decisions", "Filter", "Manual Audit"],
            ["#match-decisions-table", "#match-decision-edit-modal", "#match-decisions-feedback"],
            "/match-decisions",
            result,
        )
        capture_named_screenshot(client, result, "desktop-match-decisions-list", "/tmp/oldman-desktop-match-decisions-list.png")
        assert_list_interactions(client, "/match-decisions", "/match-decisions", "/match-decisions/table", result)
        assert_match_decision_filter_interactions(client, result)
        navigate(client, urllib.parse.urljoin(url, "/match-decisions"))
        wait_for_table_ready(client, "/match-decisions/table", "match decision edit modal", result)
        assert_match_decision_modal_edit(client, result)

        navigate(client, urllib.parse.urljoin(url, "/users"))
        assert_backend_page(
            client,
            url,
            "/users",
            ["Users", "New User", "Filter"],
            ["#users-table", "#user-password-modal", "#users-feedback"],
            "/users",
            result,
        )
        capture_named_screenshot(client, result, "desktop-users-list", "/tmp/oldman-desktop-users-list.png")
        assert_list_interactions(client, "/users", "/users", "/users/table", result)
        wait_for_table_ready(client, "/users/table", "users management modal", result)
        assert_users_management_interactions(client, url, result)

        navigate(client, urllib.parse.urljoin(url, "/user-session"))
        assert_backend_page(
            client,
            url,
            "/user-session",
            ["User Session", "Current Session", "Session Boundaries"],
            ["#user-session-feedback", "#user-session-password-modal", "#user-session-permissions-modal", "form[data-user-session-readonly-form]"],
            "/user-session",
            result,
        )
        capture_named_screenshot(client, result, "desktop-user-session", "/tmp/oldman-desktop-user-session.png")
        assert_user_session_interactions(client, result)

        navigate(client, urllib.parse.urljoin(url, "/notifications"))
        assert_backend_page(
            client,
            url,
            "/notifications",
            ["Notifications", "Pending", "Filter"],
            ["#notifications-table", "#notification-detail-modal", "#notifications-feedback", "[data-notifications-clear-selected]"],
            "/notifications",
            result,
        )
        capture_named_screenshot(client, result, "desktop-notifications-list", "/tmp/oldman-desktop-notifications-list.png")
        assert_list_interactions(client, "/notifications", "/notifications", "/notifications/table", result, require_pagination=False)
        wait_for_table_ready(client, "/notifications/table", "notifications center", result)
        assert_notifications_center_interactions(client, result)

        click_and_assert_turbo(client, 'a[href="/epg-list"]', "/epg-list", "sidebar programmes", result)
        assert_backend_page(client, url, "/epg-list", ["Programmes", "New Programme", "Filter"], ['a[href="/epg-list/new"]'], "/epg-list", result)
        capture_named_screenshot(client, result, "desktop-programmes-list", "/tmp/oldman-desktop-programmes-list.png")
        assert_list_interactions(client, "/epg-list", "/epg-list", "/epg-list/table", result)
        click_and_assert_turbo(client, 'a[href="/epg-list/new"]', "/epg-list/new", "programmes new", result)
        epg_form_assertion = client.evaluate(
            js_backend_page_assertions(
                "/epg-list/new",
                ["New Programme", "Channel", "Start Time", "Save"],
                ["form[method='post']", "input[name='csrfmiddlewaretoken']", "select[name='channel_id']"],
                "/epg-list",
            ),
            timeout=10.0,
        )
        epg_form_assertion_failures = assertion_failures(epg_form_assertion)
        result.pageErrors.extend(str(failure) for failure in epg_form_assertion_failures)
        assert_visual_health(client, "programmes new", result)
        assert_programme_datetime_picker(client, "programmes new", result)
        capture_named_screenshot(client, result, "desktop-programme-form", "/tmp/oldman-desktop-programme-form.png")
        assert_remote_select_loaded(client, "/admin/select/channels", "select[name='channel_id']", "epg channel select", result)
        assert_remote_select_search(client, "/admin/select/channels", "select[name='channel_id']", "a", "epg channel select search", result)
        assert_remote_autocomplete_search(
            client,
            "/admin/select/channels",
            "[data-om-component='autocomplete'] [data-om-autocomplete-input]",
            "a",
            "epg channel autocomplete",
            result,
        )
        assert_form_ajax_validation(client, "/epg-list/new", "start_date", result)
        assert_crud_create_edit_delete(client, url, result)

        assert_mobile_backend_pages(client, url, result)
        assert_logout_flow(client, url, result)
        client.pump(0.5)
    finally:
        if client is not None:
            client.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=3)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=3)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def main() -> int:
    """CLI entrypoint that prints the required JSON result and exits by status."""
    result = VerificationResult()
    url = os.environ.get("OLDMAN_DASHBOARD_URL", DEFAULT_URL)
    try:
        verify_dashboard(url, result)
    except Exception as exc:  # noqa: BLE001 - all failures must be reported as JSON.
        result.pageErrors.append(str(exc))

    result.ok = not result.consoleErrors and not result.pageErrors and not result.badResponses
    print(json.dumps(result.as_json(), ensure_ascii=False, indent=2), flush=True)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
