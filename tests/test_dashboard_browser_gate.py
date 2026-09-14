"""真实业务浏览器门禁脚本测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify-dashboard-browser.py"
VERIFY_TAILWIND_SCRIPT = ROOT / "scripts" / "verify-tailwind-browser.py"


def load_verify_module() -> ModuleType:
    """把带连字符的浏览器门禁脚本作为普通 Python 模块加载。"""
    spec = importlib.util.spec_from_file_location("verify_dashboard_browser", VERIFY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load verify-dashboard-browser.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    original_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


def load_tailwind_module() -> ModuleType:
    """把 Tailwind 浏览器门禁作为普通 Python 模块加载。"""
    spec = importlib.util.spec_from_file_location("verify_tailwind_browser", VERIFY_TAILWIND_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load verify-tailwind-browser.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    original_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


class DashboardBrowserGateTest(unittest.TestCase):
    """保护真实业务浏览器门禁的协议边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载浏览器门禁模块供测试复用。"""
        cls.module = load_verify_module()

    def test_expected_form_validation_response_only_allows_known_form_422(self) -> None:
        """只有门禁主动触发的业务表单 POST 422 可以从坏响应中过滤。"""
        self.assertTrue(
            self.module.is_expected_form_validation_response(
                {
                    "status": 422,
                    "url": "http://localhost:17998/channels-epg/new",
                    "requestHeaders": {"method": "POST"},
                }
            )
        )
        self.assertTrue(
            self.module.is_expected_form_validation_response(
                {
                    "status": 400,
                    "url": "http://localhost:17998/dashboard/charts/programme-trend?range=invalid",
                    "requestHeaders": {"method": "GET"},
                }
            )
        )
        self.assertTrue(
            self.module.is_expected_form_validation_response(
                {
                    "status": 400,
                    "url": "http://localhost:17998/dashboard/charts/feed-status?range=invalid",
                    "requestHeaders": {"method": "GET"},
                }
            )
        )
        self.assertTrue(
            self.module.is_expected_form_validation_response(
                {
                    "status": 400,
                    "url": "http://localhost:17998/dashboard/charts/logo-quality?range=invalid",
                    "requestHeaders": {"method": "GET"},
                }
            )
        )
        self.assertFalse(
            self.module.is_expected_form_validation_response(
                {
                    "status": 422,
                    "url": "http://localhost:17998/admin/select/channels",
                    "requestHeaders": {"method": "GET"},
                }
            )
        )
        self.assertFalse(
            self.module.is_expected_form_validation_response(
                {
                    "status": 500,
                    "url": "http://localhost:17998/channels-epg/new",
                    "requestHeaders": {"method": "POST"},
                }
            )
        )
        self.assertFalse(
            self.module.is_expected_form_validation_response(
                {
                    "status": 422,
                    "url": "http://localhost:17998/channels-epg/new",
                    "requestHeaders": {},
                }
            )
        )

    def test_tailwind_assertion_payloads_are_fail_closed(self) -> None:
        module = load_tailwind_module()

        for payload in (None, {}, [], {"failures": None}, {"failures": {}}):
            with self.subTest(payload=payload):
                self.assertTrue(module.assertions("probe", payload))
        self.assertEqual([], module.assertions("probe", {"failures": []}))

    def test_legacy_assertion_payloads_are_fail_closed(self) -> None:
        """Legacy 门禁不能把缺失或畸形的 JS 断言结果当成成功。"""
        invalid_payloads = (
            None,
            [],
            {},
            {"ok": True},
            {"failures": None},
            {"failures": {}},
            {"failures": [1]},
            {"failures": [""]},
            {"failures": ["   "]},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(self.module.VerificationError):
                self.module.assertion_failures(payload)

        self.assertEqual([], self.module.assertion_failures({"failures": []}))
        self.assertEqual(
            ["business failure"],
            self.module.assertion_failures({"failures": ["business failure"]}),
        )

        class EmptyAssertionClient:
            def evaluate(self, _expression: str, timeout: float) -> None:
                del timeout
                return None

        with self.assertRaises(self.module.VerificationError):
            self.module.assert_visual_health(
                EmptyAssertionClient(),
                "empty visual assertion",
                self.module.VerificationResult(),
            )

    def test_shell_marker_success_payload_is_fail_closed(self) -> None:
        """Shell marker 成功证据缺字段时必须中止。"""

        class MarkerClient:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def evaluate(self, _expression: str, timeout: float) -> object:
                del timeout
                return self.payload

        partial = {
            "failures": [],
            "marker": "00000000-0000-4000-8000-000000000000",
        }
        with self.assertRaises(self.module.VerificationError):
            self.module.assert_backend_shell_frame_navigation(
                MarkerClient(partial),
                self.module.VerificationResult(),
            )

    def test_table_success_payloads_are_fail_closed(self) -> None:
        """Sort 和 page size 不能用部分成功证据通过。"""

        class QueueClient:
            def __init__(self, payloads: list[dict[str, object]]) -> None:
                self.payloads = iter(payloads)

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return next(self.payloads)

        valid_sort = {
            "failures": [],
            "sort": "name",
            "ariaSort": "ascending",
            "headerClass": "om-column sorting_asc",
            "sameHead": True,
        }
        valid_sort_desc = {
            "failures": [],
            "sort": "-name",
            "ariaSort": "descending",
            "headerClass": "om-column sorting_desc",
            "sameHead": True,
        }
        common = [
            {"failures": []},
            {"failures": []},
            {"failures": []},
            {"failures": [], "skipped": True},
        ]
        cases = (
            (
                "sort partial",
                "/channels-epg",
                [*common, {"failures": [], "sort": "name"}],
            ),
            (
                "page size partial",
                "/channels-epg",
                [
                    *common,
                    valid_sort,
                    {"failures": []},
                    valid_sort_desc,
                    {"failures": []},
                    {"failures": []},
                ],
            ),
        )

        with (
            mock.patch.object(self.module, "wait_for_table_ready"),
            mock.patch.object(self.module, "install_request_probe", return_value={}),
            mock.patch.object(self.module, "clear_request_probe"),
            mock.patch.object(self.module, "wait_for_table_request"),
            mock.patch.object(self.module, "wait_for_table_success"),
        ):
            for label, path, payloads in cases:
                with self.subTest(label=label), self.assertRaises(self.module.VerificationError):
                    self.module.assert_list_interactions(
                        QueueClient(payloads),
                        path,
                        path,
                        f"{path}/table",
                        self.module.VerificationResult(),
                        require_pagination=False,
                    )

    def test_raw_modal_success_payload_requires_route(self) -> None:
        """Raw modal 的空成功证据不能跳过 endpoint 验证。"""

        class PartialClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {"failures": []}

        with (
            mock.patch.object(self.module, "clear_transient_browser_overlays"),
            mock.patch.object(self.module, "install_request_probe", return_value={}),
            mock.patch.object(self.module, "clear_request_probe"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_upstream_record_raw_modal(
                PartialClient(),
                self.module.VerificationResult(),
            )

    def test_autocomplete_success_payload_requires_value(self) -> None:
        """Autocomplete 的空成功证据不能提交空筛选值。"""

        class PartialClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {"failures": []}

        with (
            mock.patch.object(self.module, "install_request_probe", return_value={}),
            mock.patch.object(self.module, "clear_request_probe"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_upstream_catalog_feed_autocomplete_filter(
                PartialClient(),
                self.module.VerificationResult(),
            )

    def test_user_action_success_payload_requires_route(self) -> None:
        """User protection perform 返回空成功时 caller 必须拒绝。"""

        class UserClient:
            def __init__(self) -> None:
                empty_payload: dict[str, object] = {"failures": []}
                self.payloads = iter(
                    (empty_payload, empty_payload.copy(), empty_payload.copy())
                )

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return next(self.payloads)

        with (
            mock.patch.object(self.module, "install_request_probe", return_value={}),
            mock.patch.object(self.module, "clear_request_probe"),
            mock.patch.object(self.module, "wait_for_table_request"),
            mock.patch.object(self.module, "wait_for_endpoint_request"),
            mock.patch.object(self.module, "assert_user_password_login_result"),
            mock.patch.object(
                self.module,
                "perform_user_password_gate",
                return_value={
                    "failures": [],
                    "modalPath": "/users/1/password-modal",
                    "actionPath": "/users/1/password",
                },
            ),
            mock.patch.object(self.module, "perform_user_create_edit_delete_gate", return_value={"failures": []}),
            mock.patch.object(self.module, "perform_user_status_protection_gate", return_value={"failures": []}),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_users_management_interactions(
                UserClient(),
                "http://localhost:17998",
                self.module.VerificationResult(),
            )

    def test_crud_business_failures_are_not_overwritten(self) -> None:
        """Edit 的业务 failures 不能被后续 payload 展开覆盖为空。"""

        class CrudClient:
            def __init__(self) -> None:
                create: dict[str, object] = {
                    "failures": [],
                    "createPath": "/users/new",
                    "username": "browser_gate_created_1",
                    "createToast": True,
                }
                edit: dict[str, object] = {"failures": ["edit failed"]}
                self.payloads = iter((create, edit))

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return next(self.payloads)

        with (
            mock.patch.object(self.module, "clear_request_probe"),
            mock.patch.object(self.module, "navigate"),
            mock.patch.object(self.module, "wait_for_component_mounted", return_value=True),
            mock.patch.object(self.module, "wait_for_path_pattern", return_value=True),
            mock.patch.object(self.module.time, "sleep"),
        ):
            payload = self.module.perform_user_create_edit_delete_gate(
                CrudClient(),
                "http://localhost:17998",
                self.module.VerificationResult(),
            )

        self.assertEqual(["edit failed"], payload["failures"])

    def test_crud_edit_and_delete_routes_bind_the_same_user(self) -> None:
        """Delete 成功证据不能绑定到与 edit 不同的用户。"""

        class CrudClient:
            def __init__(self) -> None:
                create: dict[str, object] = {
                    "failures": [],
                    "createPath": "/users/new",
                    "username": "browser_gate_created_1",
                    "createToast": True,
                }
                edit: dict[str, object] = {
                    "failures": [],
                    "editPath": "/users/7/edit",
                    "editToast": True,
                }
                delete: dict[str, object] = {
                    "failures": [],
                    "deletePath": "/users/8/delete",
                }
                self.payloads = iter((create, edit, delete))

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return next(self.payloads)

        with (
            mock.patch.object(self.module, "clear_request_probe"),
            mock.patch.object(self.module, "navigate"),
            mock.patch.object(self.module, "wait_for_component_mounted", return_value=True),
            mock.patch.object(self.module, "wait_for_path_pattern", return_value=True),
            mock.patch.object(self.module, "wait_for_table_ready"),
            mock.patch.object(self.module.time, "sleep"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.perform_user_create_edit_delete_gate(
                CrudClient(),
                "http://localhost:17998",
                self.module.VerificationResult(),
            )

    def test_login_success_union_rejects_truthy_submitted(self) -> None:
        """Login submitted 分支不接受字符串等 truthy 证据。"""

        class LoginClient:
            load_seen = True

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "failures": [],
                    "branch": "submitted",
                    "path": "/login",
                    "hasAuthenticatedShell": False,
                    "submitted": "true",
                }

        with (
            mock.patch.object(self.module, "logout_browser_session"),
            mock.patch.object(self.module, "clear_browser_state"),
            mock.patch.object(self.module, "navigate"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.login(
                LoginClient(),
                "http://localhost:17998",
                "oldman_admin",
                "oldman_admin_123",
            )

    def test_turbo_probe_counts_are_fail_closed(self) -> None:
        """Turbo probe 的初始和轮询计数都不能缺字段。"""

        class ProbeClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "probe": "00000000-0000-4000-8000-000000000001",
                    "navigationCount": 1,
                    "navigationType": "navigate",
                    "counts": {"beforeFetch": 0},
                }

        with self.subTest("install"):
            with self.assertRaises(self.module.VerificationError):
                self.module.install_turbo_probe(ProbeClient())

        probe = "00000000-0000-4000-8000-000000000001"
        before = {
            "probe": probe,
            "navigationCount": 1,
            "navigationType": "navigate",
            "counts": {
                "beforeFetch": 0,
                "beforeRender": 0,
                "render": 0,
                "load": 0,
                "beforeFrameRender": 0,
                "frameRender": 0,
                "frameLoad": 0,
            },
        }

        class StateClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "path": "/next",
                    "probe": probe,
                    "navigationCount": 1,
                    "navigationType": "navigate",
                    "counts": {"beforeFetch": 1, "render": 1},
                    "ready": True,
                    "mainFrameState": "mounted",
                    "mainFrameReady": True,
                }

        with self.subTest("state"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_turbo_path(
                    StateClient(),
                    "/next",
                    before,
                    "partial Turbo state",
                    self.module.VerificationResult(),
                )

    def test_endpoint_request_recomputes_matched_from_requests(self) -> None:
        """Request probe 不能只用 matched=true 而没有对应请求。"""
        probe = "00000000-0000-4000-8000-000000000002"
        before = {
            "path": "/items",
            "origin": "http://oldman.local",
            "probe": probe,
            "navigationCount": 1,
        }

        class RequestClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "path": "/items",
                    "origin": "http://oldman.local",
                    "probe": probe,
                    "navigationCount": 1,
                    "requests": [],
                    "matched": True,
                }

        with self.assertRaises(self.module.VerificationError):
            self.module.wait_for_endpoint_request(
                RequestClient(),
                "/items/table",
                {"q": "gate"},
                before,
                "spoofed request",
                self.module.VerificationResult(),
            )

    def test_endpoint_request_rejects_cross_origin_path_match(self) -> None:
        """同 path/query 的跨源 URL 不能冒充当前页面请求。"""
        probe = "00000000-0000-4000-8000-000000000003"
        before = {
            "path": "/items",
            "origin": "http://oldman.local",
            "probe": probe,
            "navigationCount": 1,
        }

        class RequestClient:
            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "path": "/items",
                    "origin": "http://oldman.local",
                    "probe": probe,
                    "navigationCount": 1,
                    "requests": [
                        {
                            "type": "fetch",
                            "url": "https://evil.example/items/table?q=gate",
                            "method": "GET",
                            "headers": {},
                        }
                    ],
                    "matched": True,
                }

        with self.assertRaises(self.module.VerificationError):
            self.module.wait_for_endpoint_request(
                RequestClient(),
                "/items/table",
                {"q": "gate"},
                before,
                "cross-origin request",
                self.module.VerificationResult(),
            )

    def test_table_state_probes_reject_truthy_non_booleans(self) -> None:
        """Table 状态不能用 truthy 非布尔值冒充成功。"""

        class StateClient:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return self.payload

        ready_state = {
            "hasRoot": 1,
            "hasTable": True,
            "hasSummary": True,
            "summaryText": "1 result",
            "hasFilter": False,
            "hasFilterForm": True,
            "hasFilterControl": True,
            "hasSort": True,
        }
        with self.subTest("ready"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_table_ready(
                    StateClient(ready_state),
                    "/items/table",
                    "typed table ready",
                    self.module.VerificationResult(),
                )

        with self.subTest("success"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_table_success(
                    StateClient({"hasRoot": "yes", "status": "success", "hasRow": True}),
                    "/items/table",
                    "typed table success",
                    self.module.VerificationResult(),
                )

        with self.subTest("refresh"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_table_refresh_complete(
                    StateClient({"hasRoot": 1, "status": "success"}),
                    "/items/table",
                    "typed table refresh",
                    self.module.VerificationResult(),
                )

    def test_navigation_state_probes_reject_coerced_types(self) -> None:
        """Navigation 状态不能靠字符串强转或 truthiness 绕过。"""

        class PathValue:
            def __str__(self) -> str:
                return "/items/1/edit"

        class StateClient:
            def __init__(self, payloads: list[dict[str, object]]) -> None:
                self.payloads = payloads

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return self.payloads.pop(0)

            def pump(self, timeout: float) -> None:
                del timeout

        with self.subTest("path pattern"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_path_pattern(
                    StateClient([{"path": PathValue(), "ready": "complete"}]),
                    r"^/items/\d+/edit$",
                    "typed path pattern",
                    self.module.VerificationResult(),
                )

        with self.subTest("component"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_component_mounted(
                    StateClient([{"exists": "yes", "state": "mounted", "ready": "complete"}]),
                    "form",
                    "typed component",
                    self.module.VerificationResult(),
                )

        with self.subTest("path"):
            with self.assertRaises(self.module.VerificationError):
                self.module.wait_for_path(
                    StateClient([{"path": "/items", "ready": "complete", "omReady": True}]),
                    "/items",
                    "typed path",
                    self.module.VerificationResult(),
                )

        with (
            self.subTest("logout href"),
            mock.patch.object(self.module, "configure_viewport"),
            mock.patch.object(self.module, "navigate"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_logout_flow(
                StateClient([{"failures": [], "href": "https://evil.test/logout"}]),
                "http://oldman.local",
                self.module.VerificationResult(),
            )

        logout_client = StateClient(
            [
                {"failures": [], "href": "http://oldman.local/logout"},
                {
                    "path": "/login",
                    "ready": "complete",
                    "hasLoginForm": "yes",
                    "hasUsername": True,
                    "hasPassword": True,
                },
            ]
        )
        with (
            self.subTest("logout"),
            mock.patch.object(self.module, "configure_viewport"),
            mock.patch.object(self.module, "navigate"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_logout_flow(
                logout_client,
                "http://oldman.local",
                self.module.VerificationResult(),
            )

    def test_find_table_edit_path_binds_row_and_integer_route(self) -> None:
        """Edit path 必须来自命中行，并匹配整数主键路由。"""

        class StateClient:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return self.payload

        payloads = {
            "unbound row": {"rowCount": 0, "foundRow": False, "href": "/items/1/edit", "text": ""},
            "invented route": {"rowCount": 1, "foundRow": True, "href": "/items/evil/edit", "text": "Needle"},
        }
        for name, payload in payloads.items():
            with (
                self.subTest(name),
                mock.patch.object(self.module, "navigate"),
                mock.patch.object(self.module, "wait_for_table_ready"),
                mock.patch.object(self.module.time, "monotonic", side_effect=[0.0, 0.0, 11.0]),
                mock.patch.object(self.module.time, "sleep"),
            ):
                edit_path = self.module.find_table_edit_path(
                    StateClient(payload),
                    "http://oldman.local/items",
                    "/items/table",
                    "Needle",
                    "/items/",
                    "bound edit path",
                    self.module.VerificationResult(),
                )
                self.assertEqual(edit_path, "")

    def test_form_state_probes_recompute_same_origin_requests(self) -> None:
        """Form 门禁不能信任 matched，也不能用 path-only redirect 假通过。"""
        probe = "00000000-0000-4000-8000-000000000004"
        before = {
            "path": "/items/new",
            "origin": "http://oldman.local",
            "probe": probe,
            "navigationCount": 1,
        }

        class SequenceClient:
            def __init__(self, payloads: Sequence[Mapping[str, object]]) -> None:
                self.payloads = [dict(payload) for payload in payloads]
                self.load_seen = False

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return self.payloads.pop(0)

            def wait_for_load(self, timeout: float) -> None:
                del timeout
                self.load_seen = True

            def pump(self, seconds: float) -> None:
                del seconds

        ajax_state = {
            "path": "/items/new",
            "origin": "http://oldman.local",
            "probe": probe,
            "navigationCount": 1,
            "requests": [
                {
                    "type": "fetch",
                    "url": "https://evil.example/items/new",
                    "method": "POST",
                    "headers": {},
                }
            ],
            "matched": True,
            "status": "error",
            "hasErrorText": True,
            "fieldInvalid": "true",
        }
        with (
            self.subTest("AJAX cross-origin"),
            mock.patch.object(self.module, "install_request_probe", return_value=before),
            mock.patch.object(self.module, "clear_request_probe"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_form_ajax_validation(
                SequenceClient([{"failures": []}, ajax_state]),
                "/items/new",
                "name",
                self.module.VerificationResult(),
            )

        html_state = {
            "path": "/items/new",
            "origin": "http://oldman.local",
            "probe": probe,
            "navigationCount": 1,
            "requests": [
                {
                    "type": "xhr",
                    "url": "/items/new",
                    "method": "POST",
                    "headers": {},
                }
            ],
            "matched": True,
            "formCount": 1,
            "nestedForm": False,
            "hasErrorText": True,
            "fieldInvalid": "true",
        }
        with (
            self.subTest("HTML Accept"),
            mock.patch.object(self.module, "install_request_probe", return_value=before),
            mock.patch.object(self.module, "clear_request_probe"),
            self.assertRaises(self.module.VerificationError),
        ):
            self.module.assert_form_html_fragment_validation(
                SequenceClient([{"failures": []}, html_state]),
                "/items/new",
                "name",
                self.module.VerificationResult(),
            )

        redirect_state = {
            "path": "/items",
            "origin": "http://oldman.local",
            "ready": "complete",
            "omReady": "true",
        }
        redirect_result = self.module.VerificationResult()
        with self.subTest("full-page redirect"):
            self.module.wait_for_form_post_and_redirect(
                SequenceClient([redirect_state]),
                "/items/new",
                "/items",
                before,
                "path-only redirect",
                redirect_result,
            )
        self.assertFalse(redirect_result.pageErrors)

        cross_origin_result = self.module.VerificationResult()
        with (
            self.subTest("cross-origin redirect"),
            mock.patch.object(self.module.time, "monotonic", side_effect=[0.0, 0.0, 11.0]),
            mock.patch.object(self.module.time, "sleep"),
        ):
            self.module.wait_for_form_post_and_redirect(
                SequenceClient([{**redirect_state, "origin": "https://evil.example"}]),
                "/items/new",
                "/items",
                before,
                "cross-origin redirect",
                cross_origin_result,
            )
        self.assertTrue(cross_origin_result.pageErrors)

    def test_remote_select_and_chart_states_reject_truthy_types(self) -> None:
        """Remote Select 与 chart 状态必须使用精确字段类型。"""

        class StateClient:
            def __init__(self, payload: dict[str, object]) -> None:
                self.payload = payload

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return self.payload

        with self.subTest("remote select"):
            with self.assertRaises(self.module.VerificationError):
                self.module.assert_remote_select_loaded(
                    StateClient({"requested": "yes", "hasSelect": True, "options": ["Item"]}),
                    "/provider",
                    "select",
                    "typed remote select",
                    self.module.VerificationResult(),
                )

        chart_state = {
            "exists": 1,
            "targetExists": True,
            "emptyExists": True,
            "errorExists": True,
            "status": "success",
            "hasSvg": True,
            "emptyVisible": False,
            "emptyText": "",
        }
        with self.subTest("chart"):
            with self.assertRaises(self.module.VerificationError):
                self.module.assert_dashboard_chart(
                    StateClient(chart_state),
                    self.module.VerificationResult(),
                    "typed chart",
                    "/dashboard/charts/test",
                )

        class ChartMatrixClient:
            def __init__(self) -> None:
                payloads: tuple[dict[str, object], dict[str, object]] = (
                    {
                        "exists": True,
                        "targetExists": True,
                        "emptyExists": True,
                        "errorExists": True,
                        "status": "success",
                        "hasSvg": True,
                        "emptyVisible": True,
                        "emptyText": "No data",
                    },
                    {"failures": []},
                )
                self.payloads = iter(payloads)

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return next(self.payloads)

        with self.subTest("chart status matrix"):
            matrix_result = self.module.VerificationResult()
            self.module.assert_dashboard_chart(
                ChartMatrixClient(),
                matrix_result,
                "chart matrix",
                "/dashboard/charts/test",
            )
            self.assertTrue(matrix_result.pageErrors)

    def test_chart_3g_state_rejects_impossible_counts(self) -> None:
        """3G chart 不能省略真实图表数并伪造三个 loading 遮罩。"""

        class ChartClient:
            def command(self, _method: str, _params: dict[str, object]) -> dict[str, object]:
                return {}

            def pump(self, _duration: float) -> None:
                return None

            def evaluate(self, _expression: str, timeout: float) -> dict[str, object]:
                del timeout
                return {
                    "failures": [],
                    "fullscreenVisible": False,
                    "chartCount": 4,
                    "loadingScopeCount": 3,
                    "loadingCount": 3,
                    "overlayCount": 3,
                }

        with self.assertRaises(self.module.VerificationError):
            self.module._assert_dashboard_chart_loading_overlay_3g(
                ChartClient(),
                "http://oldman.local",
                self.module.VerificationResult(),
            )

    def test_tailwind_ready_timeout_false_is_not_success(self) -> None:
        """Tailwind omReady 超时返回 false 时必须失败。"""
        module = load_tailwind_module()

        class ReadyClient:
            expression = ""

            def evaluate(self, expression: str, timeout: float) -> bool:
                del timeout
                self.expression = expression
                return False

        client = ReadyClient()
        with self.assertRaises(module.VerificationError):
            module.wait_for_oldman_ready(client)
        self.assertIn("resolve(false)", client.expression)

    def test_navigate_timeout_fallback_binds_target_and_propagates_generic_errors(self) -> None:
        """Navigate fallback 只能接受目标 URL，且不能吞掉非超时错误。"""

        class NavigateClient:
            def __init__(self, wait_error: Exception, href: str, *, om_ready: str = "true") -> None:
                self.wait_error = wait_error
                self.href = href
                self.om_ready = om_ready
                self.load_seen = False
                self.evaluate_count = 0

            def command(self, _method: str, _params: dict[str, object]) -> dict[str, object]:
                return {}

            def wait_for_load(self, timeout: float) -> None:
                del timeout
                raise self.wait_error

            def evaluate(self, _expression: str, timeout: float) -> object:
                del timeout
                self.evaluate_count += 1
                if self.evaluate_count == 1:
                    return True
                if self.evaluate_count == 2:
                    return {"ready": "complete", "href": self.href}
                if self.evaluate_count == 3:
                    return True
                return {
                    "probe": "",
                    "ready": "complete",
                    "href": self.href,
                    "omReady": self.om_ready,
                }

            def pump(self, timeout: float) -> None:
                del timeout

        target = "http://oldman.local/items?q=gate"
        with self.subTest("correct target"):
            self.module.navigate(
                NavigateClient(self.module.PageLoadTimeout("load timeout"), target),
                target,
            )

        with self.subTest("stale wrong target"):
            with self.assertRaises(self.module.VerificationError):
                self.module.navigate(
                    NavigateClient(
                        self.module.PageLoadTimeout("load timeout"),
                        "http://oldman.local/stale?q=gate",
                    ),
                    target,
                )

        with self.subTest("runtime not ready"):
            with self.assertRaises(self.module.VerificationError):
                self.module.navigate(
                    NavigateClient(
                        self.module.PageLoadTimeout("load timeout"),
                        target,
                        om_ready="",
                    ),
                    target,
                )

        generic_error = self.module.VerificationError("websocket closed")
        with self.subTest("generic error"):
            with self.assertRaises(self.module.VerificationError) as raised:
                self.module.navigate(NavigateClient(generic_error, target), target)
            self.assertIs(raised.exception, generic_error)

    def test_preloader_navigation_rejects_error_and_stale_document(self) -> None:
        """Critical first paint 必须绑定成功的 /login 文档。"""
        module = self.module

        class PreloaderClient:
            def __init__(self, navigation_result: dict[str, object], document_url: str) -> None:
                self.navigation_result = navigation_result
                self.document_url = document_url
                self.load_seen = False
                self.commands: list[str] = []

            def command(self, method: str, _params: dict[str, object] | None = None) -> dict[str, object]:
                self.commands.append(method)
                if method == "Page.navigate":
                    return self.navigation_result
                if method == "DOM.getDocument":
                    return {"root": {"nodeId": 1, "documentURL": self.document_url}}
                return {}

            def wait_for_load(self, timeout: float) -> None:
                del timeout
                raise module.PageLoadTimeout("load timeout")

            def pump(self, timeout: float) -> None:
                del timeout

        with (
            self.subTest("errorText"),
            mock.patch.object(self.module, "logout_browser_session"),
            mock.patch.object(self.module, "clear_browser_state"),
            self.assertRaises(self.module.VerificationError),
        ):
            error_client = PreloaderClient({"errorText": "net::ERR_FAILED"}, "http://oldman.local/login")
            self.module.assert_preloader_critical_first_paint(
                error_client,
                "http://oldman.local",
                self.module.VerificationResult(),
            )

        stale_client = PreloaderClient({}, "http://oldman.local/login?stale=1")
        stale_result = self.module.VerificationResult()
        with (
            self.subTest("stale documentURL"),
            mock.patch.object(self.module, "logout_browser_session"),
            mock.patch.object(self.module, "clear_browser_state"),
        ):
            self.module.assert_preloader_critical_first_paint(
                stale_client,
                "http://oldman.local",
                stale_result,
            )
        self.assertTrue(stale_result.pageErrors)
        self.assertNotIn("DOM.querySelector", stale_client.commands)

    def test_cdp_client_uses_request_method_for_form_422_filter(self) -> None:
        """CDP response 事件缺少 method 时，门禁应使用 requestWillBeSent 记录的方法。"""
        result = self.module.VerificationResult()
        client = object.__new__(self.module.CDPClient)
        client.result = result
        client.request_methods = {}

        client._handle_event(
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "form-1",
                    "request": {"method": "POST"},
                },
            }
        )
        client._handle_event(
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "form-1",
                    "response": {
                        "status": 422,
                        "url": "http://localhost:17998/channels-epg/new",
                    },
                },
            }
        )

        self.assertEqual(result.badResponses, [])

    def test_cdp_client_records_non_cancelled_network_failures(self) -> None:
        """真实网络加载失败必须失败，取消和导航替换产生的 abort 保持豁免。"""
        cases = (
            ("connection failure", {"requestId": "request-1", "errorText": "net::ERR_CONNECTION_REFUSED"}, True),
            ("cancelled", {"requestId": "request-2", "errorText": "net::ERR_FAILED", "canceled": True}, False),
            ("navigation abort", {"requestId": "request-3", "errorText": "net::ERR_ABORTED"}, False),
            ("missing error", {"requestId": "request-4"}, False),
        )

        for label, params, should_record in cases:
            result = self.module.VerificationResult()
            client = object.__new__(self.module.CDPClient)
            client.result = result
            client.request_methods = {}

            client._handle_event({"method": "Network.loadingFailed", "params": params})

            with self.subTest(label):
                self.assertEqual(bool(result.badResponses), should_record)
                if should_record:
                    self.assertEqual(
                        result.badResponses,
                        [{"error": "net::ERR_CONNECTION_REFUSED", "requestId": "request-1"}],
                    )

    def test_list_search_gate_uses_table_filter_form_protocol(self) -> None:
        """列表页搜索门禁应提交 TableFilterForm，而不是只依赖 Table 内部搜索框。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("data-om-component='table-filter-form'", source)
        self.assertIn("data-om-table-target", source)
        self.assertIn("hasFilterControl", source)
        self.assertIn("requestSubmit()", source)

    def test_browser_gate_verifies_the_real_bold_font_resource(self) -> None:
        """Computed weight alone must not hide a synthesized DM Sans 700 face."""
        source = VERIFY_TAILWIND_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("font_loading_js", source)
        self.assertIn("document.fonts.ready", source)
        self.assertIn("DM Sans 700 FontFace", source)
        self.assertIn("700-normal", source)

    def test_list_gate_checks_full_table_demo_capabilities(self) -> None:
        """列表页浏览器门禁必须覆盖 page size、排序、多选、badge 和 action 菜单。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("data-om-table-page-size-control", source)
        self.assertIn("table page-size control is rendered above the table", source)
        self.assertIn("mobile table is still rendered as card/block layout", source)
        self.assertIn("mobile table action column is not sticky", source)
        self.assertIn("data-om-table-select-all", source)
        self.assertIn("data-om-table-select-row", source)
        self.assertIn("missing badge cell rendering", source)
        self.assertIn("action dropdown did not open", source)
        self.assertIn("sortable header still contains duplicate inline sort icon", source)
        self.assertIn("missing sortable header icon", source)
        self.assertIn("sortable header icon has no rendered mask", source)
        self.assertIn("sorting_desc", source)
        self.assertIn("filter.channel_id", source)
        self.assertIn("sort desc", source)
        self.assertIn("排序刷新替换了表头", source)
        self.assertIn("catalog channels status filter is visually collapsed", source)
        self.assertIn("catalog channels status filter id is", source)
        self.assertIn("assert_catalog_channel_edit_cancel_returns_to_list", source)
        self.assertIn('expected_query = "sort=channel_key&page_size=5"', source)
        self.assertIn('restored_page_size != "5"', source)
        self.assertIn('restored_sort_direction != "ascending"', source)
        self.assertIn("catalog channels edit cancel is not history-aware", source)
        self.assertIn("catalog channels edit cancel forces fallback navigation", source)
        self.assertIn("list query state was not restored", source)

    def test_list_gate_waits_after_page_size_before_pagination(self) -> None:
        """分页门禁必须等 page size 刷新完成，避免旧响应和第 2 页断言竞争。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        page_size_request = 'wait_for_table_request(client, table_endpoint, {"page_size": page_size, "page": "1"}, before, f"{path} page size", result)'
        page_size_success = 'wait_for_table_success(client, table_endpoint, f"{path} page size", result)'
        pagination_probe = "pagination_result = client.evaluate("

        self.assertIn(page_size_request, source)
        self.assertIn(page_size_success, source)
        self.assertLess(source.index(page_size_request), source.index(page_size_success))
        self.assertLess(source.index(page_size_success), source.index(pagination_probe))

    def test_list_gate_search_uses_seed_prefix_before_pagination(self) -> None:
        """列表门禁搜索必须命中门禁种子，避免分页在小结果集上误判。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")
        start = source.index("def assert_list_interactions")
        end = source.index("def assert_channel_names_filter_interactions")
        function_source = source[start:end]

        self.assertIn('q.value = "Browser Gate";', function_source)
        self.assertIn('input.value = "Browser Gate";', function_source)
        self.assertIn('{"q": "Browser Gate"}', function_source)
        self.assertNotIn('q.value = "a";', function_source)
        self.assertNotIn('input.value = "a";', function_source)

    def test_list_gate_uses_filter_form_helper_before_pagination(self) -> None:
        """分页门禁必须通过统一 helper 清空外部筛选表单。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")
        start = source.index("pagination_result = client.evaluate(")
        end = source.index('return { failures: ["missing page 2 button after first page size selected"] };', start)
        pagination_source = source[start:end]
        helper_start = source.index("def js_table_filter_form_helpers")
        helper_end = source.index("def clear_request_probe", helper_start)
        helper_source = source[helper_start:helper_end]

        self.assertIn("const filterForm = tableFilterFormForRoot(root);", pagination_source)
        self.assertIn("clearTableFilterForm(filterForm);", pagination_source)
        self.assertIn("filterForm.requestSubmit();", pagination_source)
        self.assertIn("form[data-om-component='table-filter-form'][data-om-table-target]", helper_source)
        self.assertIn('field instanceof HTMLInputElement && field.type === "hidden" && field.name === "csrfmiddlewaretoken"', helper_source)
        self.assertIn('field instanceof HTMLInputElement && (field.type === "checkbox" || field.type === "radio")', helper_source)
        self.assertIn("field instanceof HTMLSelectElement && field.multiple", helper_source)
        self.assertIn("option.selected = false", helper_source)

    def test_match_decision_filters_wait_for_refresh_before_next_step(self) -> None:
        """MatchDecision 筛选门禁不能只等请求发出，必须等表格刷新结束。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")
        start = source.index("def assert_match_decision_filter_interactions")
        end = source.index("def assert_match_decision_modal_edit", start)
        function_source = source[start:end]

        plain_request = '"match decisions filters",\n            result,\n        )'
        plain_success = 'wait_for_table_refresh_complete(client, "/match-decisions/table", "match decisions filters", result)'
        autocomplete_request = '"match decisions autocomplete filters",\n            result,\n        )'
        autocomplete_success = 'wait_for_table_refresh_complete(client, "/match-decisions/table", "match decisions autocomplete filters", result)'

        self.assertIn(plain_success, function_source)
        self.assertIn(autocomplete_success, function_source)
        self.assertLess(function_source.index(plain_request), function_source.index(plain_success))
        self.assertLess(function_source.index(autocomplete_request), function_source.index(autocomplete_success))
        self.assertIn("def wait_for_table_refresh_complete", source)

    def test_browser_gate_checks_preloader_critical_first_paint(self) -> None:
        """浏览器门禁必须验证 JS/CSS 未启动前 preloader 已经遮住页面。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_preloader_critical_first_paint", source)
        self.assertIn("Emulation.setScriptExecutionDisabled", source)
        self.assertIn("preloader critical first paint", source)
        self.assertIn("preloader is not covering viewport before JS", source)
        self.assertIn("preloader-critical-first-paint", source)
        self.assertIn("/tmp/oldman-preloader-critical-first-paint.png", source)
        self.assertIn("等待页面级 preloader 进入空闲状态", source)
        self.assertIn("last_failures", source)

    def test_browser_gate_checks_backend_shell_frame_navigation(self) -> None:
        """浏览器门禁必须证明后台 frame 导航不会重建 sidebar/topbar shell。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_backend_shell_frame_navigation", source)
        self.assertIn("oldman-main", source)
        self.assertIn("omFrameState", source)
        self.assertIn("shell sidebar node changed", source)
        self.assertIn("shell topbar node changed", source)
        self.assertIn("content link changed shell node", source)
        self.assertIn("sidebar active href is", source)
        self.assertIn("browser back after sidebar frame nav", source)
        self.assertIn("__oldmanMainFrameBackProbe", source)
        self.assertIn("left oldman-main loading", source)

    def test_browser_gate_checks_scoped_preloader_for_frame_navigation(self) -> None:
        """内容区 Turbo Frame 导航不能触发全屏 preloader，必须使用 oldman-main 作用域加载反馈。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("oldman-main scoped preloader is missing", source)
        self.assertIn("fullscreen preloader displayed during main frame navigation", source)
        self.assertIn("data-om-scoped-preloader", source)
        self.assertIn("omPreloaderStatus", source)
        self.assertIn(':scope > [data-om-scoped-preloader]', source)
        self.assertIn('wait_for_table_refresh_complete(client, "/channels-epg/table"', source)

    def test_tailwind_gate_uses_full_route_matrix_and_screenshots(self) -> None:
        """Tailwind 门禁必须逐主要路由保存桌面和移动截图，而不是少数页面冒烟。"""
        source = VERIFY_TAILWIND_SCRIPT.read_text(encoding="utf-8")

        for route in (
            "/users",
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
        ):
            self.assertIn(f'"{route}"', source)

        self.assertIn("SCREENSHOT_DIR", source)
        self.assertIn('route:{path}:desktop', source)
        self.assertIn('route:{path}:mobile', source)
        self.assertIn("route_structure_js", source)
        self.assertIn("expected exactly one h1", source)
        self.assertIn("legacy page-title-box remains", source)

    def test_tailwind_gate_uses_business_specific_modal_targets(self) -> None:
        """Tailwind 门禁必须检查业务 modal，不能让全局 notification modal 误通过。"""
        source = VERIFY_TAILWIND_SCRIPT.read_text(encoding="utf-8")

        for target in (
            "#user-password-modal",
            "#user-status-modal",
            "#user-delete-modal",
            "#user-session-password-modal",
            "#upstream-records-help",
            "#upstream-record-raw-modal",
            "#logo-asset-compare-modal",
            "#match-decision-edit-modal",
            "#notification-detail-modal",
        ):
            self.assertIn(target, source)

        self.assertIn("openRowActionModal", source)
        self.assertIn("BUSINESS_MODAL_CHECKS", source)
        self.assertIn("business_modal_js", source)
        self.assertNotIn('querySelector("[data-om-modal-target]")', source)
        self.assertNotIn("#removeNotificationModal", source)

    def test_browser_gate_checks_business_autocomplete_provider_search(self) -> None:
        """真实业务浏览器门禁必须覆盖 Autocomplete provider 搜索请求。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_remote_autocomplete_search", source)
        self.assertIn("data-om-autocomplete-input", source)
        self.assertIn("/admin/select/channels", source)
        self.assertIn("epg channel autocomplete", source)

    def test_browser_gate_covers_real_crud_edit_delete_and_mobile_pages(self) -> None:
        """真实业务浏览器门禁必须覆盖编辑/删除路径和移动端列表表单页面。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_crud_create_edit_delete", source)
        self.assertIn("Oldman Browser Gate", source)
        self.assertIn("assert_delete_form_redirects", source)
        self.assertIn("Danger Zone", source)
        self.assertIn("assert_mobile_backend_pages", source)
        self.assertIn("mobile-channels-list", source)
        self.assertIn("mobile-programme-form", source)
        self.assertIn("assert_logout_flow", source)
        self.assertIn('a[href="/logout"]', source)
        self.assertIn("assert_programme_datetime_picker", source)
        self.assertIn("assert_programme_channel_remote_fields", source)
        self.assertIn("channel autocomplete hidden value is empty", source)
        self.assertIn("channel autocomplete did not render selectable suggestions", source)
        self.assertIn('input[name="start_date"]', source)
        self.assertIn("enableTime", source)
        self.assertIn("assert_native_datetime_local_control", source)
        self.assertIn('input.type = "datetime-local"', source)
        self.assertIn('"2026-06-10T12:30"', source)

    def test_browser_gate_seeds_required_dev_data_before_login(self) -> None:
        """oldman_dev 空库门禁必须在登录和页面交互前准备完整业务数据。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")
        verify_source = source.split("def verify_dashboard", 1)[1]

        self.assertLess(
            verify_source.index("asyncio.run(ensure_dashboard_browser_gate_records())"),
            verify_source.index("login(\n            client"),
        )
        self.assertIn("ensure_epg_gate_records", source)
        self.assertIn("CatalogLogoAsset", source)
        self.assertIn("ChannelsEpg", source)
        self.assertIn("ChannelName", source)
        self.assertIn("EpgList", source)

    def test_browser_gate_login_refresh_is_idempotent(self) -> None:
        """长流程中刷新登录时，已认证会话不能被误判成缺少登录表单。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('branch: "already-authenticated"', source)
        self.assertIn("hasAuthenticatedShell", source)
        self.assertIn("已登录时保持当前有效会话", source)

    def test_browser_gate_checks_visible_element_viewport_bounds(self) -> None:
        """视觉门禁必须发现被 overflow-x-hidden 裁掉的可见卡片或行。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("viewportBoundedSelectors", source)
        self.assertIn("visible element overflows viewport", source)
        self.assertIn("insideHorizontalScroller", source)
        self.assertIn('".row"', source)
        self.assertIn('".card"', source)

    def test_browser_gate_dev_seed_supports_first_page_pagination(self) -> None:
        """门禁种子数量必须超过表格第一档 page size，不能靠假分页通过。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("required_count = ChannelsEpgTable.page_size_options[0] + 1", source)
        self.assertIn("required_count = LogoAssetTable.page_size_options[0] + 1", source)
        self.assertIn(".limit(required_count)", source)

    def test_browser_gate_records_multiple_visual_screenshots(self) -> None:
        """浏览器门禁应输出多页面截图路径，方便人工抽查视觉状态。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("screenshots", source)
        self.assertIn("desktop-channels-list", source)
        self.assertIn("desktop-programme-form", source)
        self.assertIn("mobile-programmes-list", source)

    def test_browser_gate_checks_dashboard_programme_chart(self) -> None:
        """浏览器门禁必须覆盖真实 Dashboard 多图表挂载、空态和错误态。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("programme trend chart", source)
        self.assertIn("feed status chart", source)
        self.assertIn("logo quality chart", source)
        self.assertIn("data-om-component='apex-chart'", source)
        self.assertIn("data-om-chart-src", source)
        self.assertIn("/dashboard/analytics", source)
        self.assertIn("desktop-dashboard-analytics", source)
        self.assertIn("mobile-dashboard-analytics", source)
        self.assertIn("/dashboard/charts/programme-trend", source)
        self.assertIn("/dashboard/charts/feed-status", source)
        self.assertIn("/dashboard/charts/logo-quality", source)
        self.assertIn("data-om-chart-empty", source)
        self.assertIn("data-om-chart-error", source)
        self.assertIn("assert_dashboard_chart_loading_overlay_3g", source)
        self.assertIn("Target.closeTarget", source)
        self.assertIn("Network.emulateNetworkConditions", source)
        self.assertIn("data-om-scoped-preloader", source)
        self.assertIn("legacy text loading is visible", source)
        self.assertIn("/tmp/oldman-dashboard-chart-loading-3g.png", source)

    def test_browser_gate_checks_dashboard_overview_interactions(self) -> None:
        """浏览器门禁必须点击首页 range、refresh toast、failure feedback 和 notification。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_dashboard_overview_interactions", source)
        self.assertIn("def assert_dashboard_overview_interactions", source)
        self.assertIn("install_request_probe(client)\n    interaction_result", source)
        self.assertIn("data-om-dashboard-range", source)
        self.assertIn("/dashboard/charts/programme-trend", source)
        self.assertIn("/dashboard/charts/feed-status", source)
        self.assertIn("/dashboard/charts/logo-quality", source)
        self.assertIn("data-om-dashboard-refresh", source)
        self.assertIn('invalidRange.dataset.omDashboardRange = "invalid"', source)
        self.assertIn("chart failure did not request ${path} invalid range", source)
        self.assertIn("refresh did not show dashboard success toast", source)
        self.assertIn(".swal2-popup", source)
        self.assertIn("notificationDropdown", source)
        self.assertIn("notification-check", source)
        self.assertIn('data-om-modal-target="#removeNotificationModal"', source)
        self.assertIn("removeNotificationModal did not open", source)
        self.assertIn("delete-notification", source)

    def test_browser_gate_checks_match_decision_modal_submit(self) -> None:
        """浏览器门禁必须真实覆盖 MatchDecision modal 非法和合法提交。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_match_decision_modal_edit", source)
        self.assertIn("/match-decisions", source)
        self.assertIn("/match-decisions/table", source)
        self.assertIn("#match-decision-edit-modal", source)
        self.assertIn("match decision invalid submit", source)
        self.assertIn("match decision valid submit", source)
        self.assertIn("filter.decision", source)
        self.assertIn("filter.decided_by", source)
        self.assertIn("filter.source_record_id", source)
        self.assertIn("filter.catalog_channel_id", source)
        self.assertIn("filter.catalog_feed_id", source)
        self.assertIn("/admin/select/upstream_records", source)
        self.assertIn("match decision reason native validation", source)
        self.assertIn("match decision modal has duplicate visible footers", source)
        self.assertIn('CatalogMatchDecision.decided_by == "browser-gate"', source)
        self.assertIn("browser_gate_match_count", source)
        self.assertIn('CatalogChannel.channel_key.like("browser-gate-match-%")', source)
        self.assertIn('UpstreamSourceRecord.primary_name.like("Browser Gate Match Source%")', source)

    def test_browser_gate_checks_users_management_interactions(self) -> None:
        """浏览器门禁必须真实覆盖 Users 页面筛选、密码、禁用和删除交互。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_users_management_interactions", source)
        self.assertIn("/users", source)
        self.assertIn("/users/table", source)
        self.assertIn("filter.is_active", source)
        self.assertIn("filter.is_superuser", source)
        self.assertIn("#user-password-modal", source)
        self.assertIn("user password mismatch", source)
        self.assertIn("user weak password submitted ajax request", source)
        self.assertIn("user password pattern missing", source)
        self.assertIn("user password modal has duplicate visible footers", source)
        self.assertIn("user password valid submit", source)
        self.assertIn("users password change did not add topbar notification", source)
        self.assertIn("#user-status-modal", source)
        self.assertIn("cannot disable current user", source)
        self.assertIn("#user-delete-modal", source)
        self.assertIn("cannot delete superuser", source)
        self.assertIn("users success toast", source)
        self.assertIn("perform_user_create_edit_delete_gate", source)
        self.assertIn("users create success toast missing", source)
        self.assertIn("users edit success toast missing", source)
        self.assertIn("created user delete success toast missing", source)
        self.assertIn("users password old password still logs in", source)

    def test_browser_gate_checks_notifications_center_interactions(self) -> None:
        """浏览器门禁必须真实覆盖通知中心筛选、批量清除和详情弹窗。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_notifications_center_interactions", source)
        self.assertIn("notification stat card", source)
        self.assertIn("avatar-title expanded", source)
        self.assertIn("icon overlaps value", source)
        self.assertIn("/notifications", source)
        self.assertIn("/notifications/table", source)
        self.assertIn("filter.notification_type", source)
        self.assertIn("filter.severity", source)
        self.assertIn("notification center selected clear toast missing", source)
        self.assertIn("data-notifications-clear-selected", source)
        self.assertIn("#notification-detail-modal", source)
        self.assertIn("notification detail modal did not open", source)
        self.assertIn("assert_visible_modal_fits", source)
        self.assertIn("modal child overflows content", source)
        self.assertIn("notification center empty state missing after clear", source)
        self.assertIn("clear_transient_browser_overlays(client)", source)
        self.assertIn('wait_for_table_success(client, "/notifications/table", "notifications filters", result)', source)
        self.assertIn("notifications topbar missing browser gate notification", source)
        self.assertIn("filter((item) => item.textContent.includes(\"Browser Gate Notification\"))", source)
        self.assertIn(".swal2-container", source)
        self.assertIn(".modal-backdrop", source)

    def test_browser_gate_checks_user_session_interactions(self) -> None:
        """浏览器门禁必须覆盖当前会话页、远程密码弹窗、顶栏菜单和退出。"""
        source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_user_session_interactions", source)
        self.assertIn("/user-session", source)
        self.assertIn("#user-session-feedback", source)
        self.assertIn("#user-session-password-modal", source)
        self.assertIn("/user-session/password-modal", source)
        self.assertIn("/user-session/password", source)
        self.assertIn("user session password mismatch", source)
        self.assertIn("user session password modal has duplicate visible footers", source)
        self.assertIn("user session password valid submit", source)
        self.assertIn("user session topbar dropdown did not open", source)
        self.assertIn("user session permissions modal did not open", source)
        self.assertIn("navigate(client, urllib.parse.urljoin(base_url, \"/user-session\"))", source)
        self.assertIn('a[href="/logout"]', source)


if __name__ == "__main__":
    unittest.main()
