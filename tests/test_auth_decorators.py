"""后台页面认证装饰器测试。"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any, cast

from apps.auth.session import DashboardSessionData
from sanic.response import text

from oldman.web.session import Session


def response_body(response: object) -> bytes:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise AssertionError("response has no byte body")
    return body


class AdminRequiredDecoratorTest(unittest.TestCase):
    """验证 admin_required 的登录、staff 和响应模式协议。"""

    def test_unauthenticated_html_request_redirects_to_login_with_next(self) -> None:
        """未登录 HTML 请求返回 302，并保留当前 path/query。"""
        from apps.auth.decorators import admin_required

        response = asyncio.run(protected_response(admin_required(), make_request(path="/channels", query_string="page=2")))

        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login?next=%2Fchannels%3Fpage%3D2")

    def test_unauthenticated_json_request_returns_login_endpoint(self) -> None:
        """未登录 JSON 请求返回 401 和登录入口，不执行 UI Action。"""
        from apps.auth.decorators import admin_required

        from oldman.web.api.enums import ApiErrorCode

        response = asyncio.run(protected_response(admin_required(), make_request(headers={"accept": "application/json"})))
        payload = json.loads(response_body(response))

        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error_code"], ApiErrorCode.AUTHENTICATION_REQUIRED)
        self.assertEqual(payload["actions"], [])
        self.assertEqual(payload["data"]["login_url"], "/login")

    def test_unauthenticated_oldman_html_request_returns_json_401(self) -> None:
        """Oldman 局部 HTML 请求不能跟随 302 后误收完整登录页。"""
        from apps.auth.decorators import admin_required

        response = asyncio.run(
            protected_response(
                admin_required(),
                make_request(headers={"accept": "text/html", "x-requested-with": "XMLHttpRequest"}),
            )
        )
        payload = json.loads(response_body(response))

        self.assertEqual(response.status, 401)
        self.assertEqual(payload["data"]["login_url"], "/login")
        self.assertEqual(payload["actions"], [])

    def test_response_mode_parameter_overrides_accept_header(self) -> None:
        """response_mode 参数优先于 Accept。"""
        from apps.auth.decorators import admin_required

        response = asyncio.run(
            protected_response(
                admin_required(),
                make_request(args={"response_mode": "html"}, headers={"accept": "application/json"}),
            )
        )

        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login?next=%2Fdemo")

    def test_authenticated_staff_request_reaches_handler(self) -> None:
        """已登录且 is_staff=True 时允许进入业务 handler。"""
        from apps.auth.decorators import admin_required

        response = asyncio.run(
            protected_response(
                admin_required(),
                make_request(session=DashboardSessionData(user_id=7, is_active=True, is_staff=True)),
            )
        )

        self.assertEqual(response.body, b"handler-ok")

    def test_authenticated_non_staff_request_is_denied_by_mode(self) -> None:
        """已登录但非 staff 请求按响应模式返回 403。"""
        from apps.auth.decorators import admin_required

        from oldman.web.api.enums import ApiErrorCode

        json_response = asyncio.run(
            protected_response(
                admin_required(),
                make_request(
                    session=DashboardSessionData(user_id=7, is_active=True, is_staff=False),
                    headers={"accept": "application/json"},
                ),
            )
        )
        payload = json.loads(response_body(json_response))
        self.assertEqual(json_response.status, 403)
        self.assertEqual(payload["error_code"], ApiErrorCode.PERMISSION_DENIED)

        async def check_html_permission():
            """Render the actual error page with a real Sanic request/environment."""
            from uuid import uuid4
            from sanic import Sanic
            from sanic_ext import Config, Extend
            from sanic_ext.extensions.templating.extension import TemplatingExtension
            from oldman.web.errors import OldmanErrorHandler

            app = Sanic(f"demo-permission-{uuid4().hex}", error_handler=OldmanErrorHandler())
            Extend(app, config=Config(templating_enable_async=True), extensions=[TemplatingExtension], built_in_extensions=False)
            app.ctx.session = make_request().app.ctx.session

            @app.on_request
            async def logged_in(request):
                request.ctx.session = DashboardSessionData(user_id=7, is_active=True, is_staff=False)

            @app.get("/denied")
            @admin_required()
            async def endpoint(request):
                raise AssertionError("denied handler must not execute")

            _, response = await app.asgi_client.get("/denied", headers={"accept": "text/html"})
            self.assertEqual(response.status, 403)
            self.assertEqual(response.content_type, "text/html; charset=utf-8")
            self.assertIn("Access denied", response.text)

        asyncio.run(check_html_permission())


async def protected_response(decorator, request):
    """执行被 admin_required 包装的测试 handler。"""

    @decorator
    async def handler(_request):
        return text("handler-ok")

    return await handler(request)


class Args(dict):
    """Sanic request.args 兼容对象。"""


def make_request(
    *,
    args: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    path: str = "/demo",
    query_string: str = "",
    session: DashboardSessionData | None = None,
):
    """构造 admin_required 测试请求。"""
    session_data = session or DashboardSessionData()
    session_manager = Session()
    session_manager.interface = cast(Any, SimpleNamespace(session_name="session"))
    return SimpleNamespace(
        args=Args(args or {}),
        headers=headers or {},
        path=path,
        query_string=query_string,
        ctx=SimpleNamespace(session=session_data),
        app=SimpleNamespace(ctx=SimpleNamespace(session=session_manager)),
    )


if __name__ == "__main__":
    unittest.main()
