"""Sanic Web 应用工厂测试。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from apps.examples.session import DashboardSessionData
from apps.epg_admin.forms import (
    CatalogFeedForm,
    ChannelsEpgForm,
    EpgListForm,
    LogoAssetFilterForm,
    MatchDecisionFilterForm,
    NotificationFilterForm,
    UpstreamRecordFilterForm,
)
from apps.epg_admin.tables import ChannelsEpgTable
from config.settings import settings
from sanic import Sanic
from sanic.exceptions import SanicException
from services.web import (
    WebService,
    install_dashboard_templates,
)

from oldman.web.api import accepts_html_form_response, accepts_json_form_response, form_invalid_response
from oldman.runtime.discovery import (
    discover_service_definitions,
    load_service_class,
)
from oldman.web.auth import UserPasswordForm
from oldman.web.authentication import RequestUser
from oldman.web.components.forms import AjaxSelectField, AjaxSelectWidget
from oldman.web.components.selects import select_registry
from oldman.web.components.tables import TableResult
from oldman.web.package_data import package_template_dir
from oldman.web.security import WebSecurityPurpose, configured_web_security_key

SELECT_BINDING_SECRET = configured_web_security_key(
    WebSecurityPurpose.SELECT_BINDING
)
ROOT = Path(__file__).resolve().parents[1]


def response_body(response: object) -> bytes:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise AssertionError("response has no byte body")
    return body


async def _page_must_not_render():
    """Fragment and JSON clients never reach the full-page branch."""
    raise AssertionError("the full page must not be rendered for this client")


class WebAppTest(unittest.TestCase):
    """验证应用工厂可以正确组装当前 Web 页面。"""

    app: Sanic

    @classmethod
    def setUpClass(cls) -> None:
        """创建共享应用，避免每个用例重复触发 freetv 风格路由模块导入。"""
        cls.app = create_test_app()

    def test_create_app_uses_project_name(self) -> None:
        """Sanic 应用应该使用项目配置中的应用名。"""
        self.assertEqual(self.app.name, "oldman")
        self.assertFalse(hasattr(self.app.ctx, "settings"))

    def test_table_summary_without_i18n_formats_parameters(self) -> None:
        """关闭 i18n 后仍使用真实 Demo 接线渲染参数化摘要，包括空表。"""
        from oldman.i18n.translations import current_translations

        environment = self.app.ext.environment
        token = current_translations.set(None)
        try:
            # 初始化 helper 会同时修改共享模板变量和资源注册表，失败时也要恢复。
            with (
                patch.object(settings.i18n, "use_i18n", False),
                patch.dict(environment.globals),
                patch.object(self.app.ctx, "static_bundle_registry", self.app.ctx.static_bundle_registry),
            ):
                install_dashboard_templates(self.app)
                template = environment.get_template("oldman/tables/default/summary.html")
                for total, rows, expected in (
                    (25, [None] * 10, "Showing 1 to 10 of 25 entries"),
                    (0, [], "Showing 0 to 0 of 0 entries"),
                ):
                    with self.subTest(total=total):
                        content = asyncio.run(template.render_async(
                            result=SimpleNamespace(filtered_total=total, page=1, page_size=10, rows=rows),
                        ))
                        self.assertIn(expected, content)
                self.assertEqual(environment.globals["gettext"]("Count: %(count)s", count=3), "Count: 3")
        finally:
            current_translations.reset(token)

    def test_oldman_discovers_web_service(self) -> None:
        """Oldman 约定式服务发现应该能找到 WebService。"""
        definitions = discover_service_definitions(ROOT)

        self.assertEqual(tuple(definitions), ("nats_a", "nats_b", "task_scheduler", "task_worker", "web"))
        self.assertEqual(definitions["nats_a"].application_base, "simple")
        self.assertEqual(definitions["nats_b"].application_base, "simple")
        self.assertEqual(definitions["task_worker"].application_base, "taskiq_worker")
        self.assertEqual(definitions["task_scheduler"].application_base, "taskiq_scheduler")
        self.assertIs(load_service_class(definitions["web"]), WebService)

    def test_dashboard_session_uses_the_configured_typed_redis_interface(self) -> None:
        """The Web runtime must install the concrete Dashboard Session model."""
        interface = cast(Any, self.app.ctx.session.interface)

        self.assertTrue(settings.web.session.enabled)
        self.assertEqual(interface.redis_alias, "SESSION")
        self.assertIs(interface.session_model, DashboardSessionData)
        self.assertIs(WebService.SESSION_MODEL, DashboardSessionData)

    def test_web_service_exposes_dev_command(self) -> None:
        """web 服务应该提供显式开发模式命令。"""
        commands = WebService.get_default_commands()

        self.assertIn("dev", commands)
        self.assertIn("development service", commands["dev"][1])

    def test_dashboard_route_is_registered(self) -> None:
        """根仪表盘路由应该被注册到 Sanic 路由表。"""
        route_names = set(self.app.router.name_index)
        self.assertIn(f"{self.app.name}.dashboard", route_names)
        self.assertIn(f"{self.app.name}.dashboard_analytics", route_names)

    def test_static_route_is_registered(self) -> None:
        """静态资源路由应该被注册到 Sanic 路由表。"""
        self.assertIn(f"{self.app.name}.static", self.app.router.name_index)

    def test_dashboard_template_uses_real_business_content(self) -> None:
        """仪表盘模板应该渲染真实后台统计，而不是旧的静态演示页。"""
        template_path = settings.web.template.dir / "pages" / "dashboard.html"
        template_source = template_path.read_text(encoding="utf-8")
        analytics_template = (settings.web.template.dir / "pages" / "dashboard_analytics.html").read_text(encoding="utf-8")
        sidebar_source = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("stats.channel_count", template_source)
        self.assertIn("Recent Upstream Anomalies", template_source)
        self.assertIn("Recent Decisions", template_source)
        # 侧边栏用框架的 sidebar_menu_item 宏输出链接，源码里是宏调用而不是裸 href。
        self.assertIn('sidebar_menu_item("/dashboard", _("Overview")', sidebar_source)
        self.assertIn('sidebar_menu_item("/dashboard/analytics", _("Analytics")', sidebar_source)
        self.assertIn("Dashboard", sidebar_source)
        self.assertIn("Overview", sidebar_source)
        self.assertIn("Analytics", sidebar_source)
        self.assertIn('data-om-component="dashboard-overview"', analytics_template)
        self.assertIn("programme_trend_chart.render_shell", analytics_template)
        self.assertIn("feed_status_chart.render_shell", analytics_template)
        self.assertIn("logo_quality_chart.render_shell", analytics_template)
        self.assertNotIn('src/pages/dashboard.ts', template_source)
        self.assertNotIn("Live Users By Country", template_source)

    def test_topbar_language_links_use_freetv_style_data_lang(self) -> None:
        """顶栏语言链接应该使用 freetv 风格 data-lang 协议。"""
        topbar_source = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")
        shared_language_source = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "language_switcher.html"
        ).read_text(encoding="utf-8")

        # The topbar includes the framework partial directly; it reads the language state from the shared globals.
        self.assertIn('include "oldman/dashboard/partials/language_switcher.html"', topbar_source)
        self.assertIn("language_menu_items(request)", shared_language_source)
        self.assertIn('data-om-component="dropdown"', shared_language_source)
        self.assertIn('data-om-dropdown-toggle', shared_language_source)
        self.assertIn(
            'data-lang="{{ language.code }}"',
            shared_language_source,
        )
        self.assertNotIn('data-bs-toggle="dropdown"', shared_language_source)

    def test_dashboard_requires_login(self) -> None:
        """未登录访问后台首页最终应该进入登录页。"""
        _request, response = self.app.test_client.get("/")

        self.assertEqual(response.status, 200)
        self.assertIn('data-om-page="login"', response.text)
        # The framework's sign-in page: every active account signs in, as in the skeleton.
        self.assertIn("Sign in with your account to continue.", response.text)
        self.assertIn('name="next" value="/"', response.text)

    def test_password_reset_is_installed_at_its_setting_and_the_sign_in_page_links_it(self) -> None:
        """web.account.password_reset_url turns the framework's reset flow on (apps/accounts/routes.py)."""
        self.assertEqual("/password-reset", settings.web.account.password_reset_url)
        _request, response = self.app.test_client.get("/login")
        self.assertIn('href="/password-reset"', response.text)
        self.assertIn("Forgot password?", response.text)
        _request, response = self.app.test_client.get("/password-reset")
        self.assertEqual(200, response.status)
        self.assertIn('data-om-page="login"', response.text)
        route_paths = {route.path for route in self.app.router.routes}
        for path in ("password-reset", "password-reset/sent", "password-reset/done", "password-reset/<uidb64:str>/<token:str>"):
            self.assertIn(path, route_paths)

    def test_token_routes_hand_tokens_to_every_active_account(self) -> None:
        """令牌接口和登录页一样认所有启用的账户(启用是登录的底线),三个接口都只收 POST。"""
        from apps.accounts.models import User
        from apps.accounts.tokens import token_flow

        member = User(id=5, username="member", password_hash="", is_active=True, is_staff=False, is_superuser=False)
        self.assertTrue(token_flow.accept_user(member))
        route_methods = {route.path: route.methods for route in self.app.router.routes}
        for path in ("api/token", "api/token/refresh", "api/token/revoke"):
            self.assertEqual({"POST"}, route_methods.get(path))

    def test_login_response_renders_tailwind_auth_shell(self) -> None:
        """登录页应该渲染当前 Tailwind 登录结构。"""
        _request, response = self.app.test_client.get("/login")

        self.assertEqual(response.status, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('<link rel="icon" href="data:,">', response.text)
        self.assertIn('data-om-page="login"', response.text)
        self.assertIn("oldman-brand-mark", response.text)
        self.assertIn(str(settings.core.site_name or settings.core.app_name), response.text)
        self.assertIn('action="/login"', response.text)
        self.assertIn('name="csrfmiddlewaretoken"', response.text)
        self.assertIn('name="username"', response.text)
        self.assertIn('name="password"', response.text)

    def test_login_response_uses_the_request_language(self) -> None:
        """服务端 HTML language 必须来自统一请求语言，而不是模板 fallback。"""
        _request, response = self.app.test_client.get(
            "/login",
            headers={"Cookie": "preferred_language=zh-hans"},
        )

        self.assertEqual(response.status, 200)
        self.assertIn('<html lang="zh-Hans"', response.text)
        self.assertNotIn("http://localhost:5173", response.text)
        self.assertNotIn("/@vite/client", response.text)
        self.assertNotIn("auth-signup-basic.html", response.text)
        self.assertNotIn("auth-page-wrapper", response.text)

    def test_login_error_page_still_renders_csrf_token(self) -> None:
        """登录失败回跳页应该由 GET 重新渲染错误和 CSRF token。"""
        _request, response = self.app.test_client.get("/login?next=%2F&error=invalid_credentials")

        self.assertEqual(response.status, 200)
        self.assertIn("Invalid username or password.", response.text)
        self.assertIn('name="csrfmiddlewaretoken"', response.text)
        self.assertIn('name="next" value="/"', response.text)

    def test_epg_admin_templates_keep_oldman_table_and_form_structure(self) -> None:
        """EPG 后台模板应该挂载后端表单和表格对象。"""
        catalog_channels_index = (settings.web.template.dir / "pages" / "catalog_channels" / "index.html").read_text(encoding="utf-8")
        catalog_channels_form = (settings.web.template.dir / "pages" / "catalog_channels" / "form.html").read_text(encoding="utf-8")
        catalog_feeds_index = (settings.web.template.dir / "pages" / "catalog_feeds" / "index.html").read_text(encoding="utf-8")
        catalog_feeds_form = (settings.web.template.dir / "pages" / "catalog_feeds" / "form.html").read_text(encoding="utf-8")
        upstream_records_index = (settings.web.template.dir / "pages" / "upstream_records" / "index.html").read_text(encoding="utf-8")
        logo_assets_index = (settings.web.template.dir / "pages" / "logo_assets" / "index.html").read_text(encoding="utf-8")
        match_decisions_index = (settings.web.template.dir / "pages" / "match_decisions" / "index.html").read_text(encoding="utf-8")
        channels_index = (settings.web.template.dir / "pages" / "channels_epg" / "index.html").read_text(encoding="utf-8")
        channel_names_index = (settings.web.template.dir / "pages" / "channel_names" / "index.html").read_text(encoding="utf-8")
        epg_form = (settings.web.template.dir / "pages" / "epg_list" / "form.html").read_text(encoding="utf-8")

        self.assertIn("filter_form.render", catalog_channels_index)
        self.assertIn("table.render_shell", catalog_channels_index)
        self.assertIn('data-om-component="slider"', catalog_channels_index)
        self.assertIn('data-om-modal-target="#catalog-channel-evidence-modal"', catalog_channels_index)
        self.assertIn('href="/catalog-channels" class="om-button om-button-secondary"', catalog_channels_form)
        self.assertIn('form.render(cancel_url="/catalog-channels", form_mode="json")', catalog_channels_form)
        self.assertNotIn("return_url", catalog_channels_form)
        self.assertNotIn("form_action", catalog_channels_form)
        self.assertIn("filter_form.render", catalog_feeds_index)
        self.assertIn("table.render_shell", catalog_feeds_index)
        self.assertIn("form.render", catalog_feeds_form)
        self.assertIn('data-om-component="upload"', catalog_feeds_form)
        self.assertIn("does not update CatalogLogoAsset", catalog_feeds_form)
        self.assertIn("filter_form.render", upstream_records_index)
        self.assertIn("table.render_shell", upstream_records_index)
        self.assertIn('data-om-component="list"', upstream_records_index)
        self.assertIn('"upstream-records-help"', upstream_records_index)
        self.assertIn('"upstream-record-raw-modal"', upstream_records_index)
        self.assertIn("filter_form.render", logo_assets_index)
        self.assertIn("table.render_shell", logo_assets_index)
        self.assertIn('data-om-component="slider"', logo_assets_index)
        self.assertIn("quality_distribution_chart.render_shell", logo_assets_index)
        self.assertIn("mime_distribution_chart.render_shell", logo_assets_index)
        self.assertIn("dimension_scatter_chart.render_shell", logo_assets_index)
        self.assertIn('"logo-asset-compare-modal"', logo_assets_index)
        self.assertIn("filter_form.render", match_decisions_index)
        self.assertIn("table.render_shell", match_decisions_index)
        self.assertIn('component="modal"', match_decisions_index)
        self.assertIn("remote_content=true", match_decisions_index)
        self.assertIn('id="match-decisions-feedback"', match_decisions_index)
        match_decision_modal_source = match_decisions_index.split('"match-decision-edit-modal"', 1)[1]
        self.assertNotIn('data-om-modal-close>{{ _("Close") }}</button>', match_decision_modal_source)
        match_decisions_edit = (settings.web.template.dir / "partials" / "match_decisions" / "edit_form.html").read_text(encoding="utf-8")
        self.assertIn('data-om-component="form"', match_decisions_edit)
        self.assertIn('data-om-component="form-validator"', match_decisions_edit)
        self.assertIn("filter_form.render", channels_index)
        self.assertIn("table.render_shell", channels_index)
        self.assertIn("filter_form.render", channel_names_index)
        self.assertIn("table.render_shell", channel_names_index)
        self.assertNotIn("table.render()", channels_index)
        self.assertIn("form.render", epg_form)
        self.assertIn('class="om-card', epg_form)

    def test_base_and_login_templates_mount_standard_preloader(self) -> None:
        """基础后台页挂载标准 preloader 组件(登录页是框架的,它的 head 在框架测试里)。"""
        base_template = (settings.web.template.dir / "base.html").read_text(encoding="utf-8")
        shared_base_template = (package_template_dir() / "oldman" / "dashboard" / "base.html").read_text(encoding="utf-8")
        shared_shell_template = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "shell.html"
        ).read_text(encoding="utf-8")
        sidebar_template = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")
        partial = (package_template_dir() / "oldman" / "dashboard" / "partials" / "preloader.html").read_text(encoding="utf-8")
        critical_css = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "preloader_critical_css.html"
        ).read_text(encoding="utf-8")

        self.assertIn('{% extends "oldman/dashboard/base.html" %}', base_template)
        self.assertIn('data-preloader="enable"', shared_base_template)
        self.assertIn('id="oldman-sidebar-nav"', shared_shell_template)
        self.assertIn('target="oldman-main"', shared_shell_template)
        self.assertIn('id="oldman-main"', shared_shell_template)
        self.assertIn('data-turbo-action="advance"', shared_shell_template)
        self.assertIn('class="oldman-main"', shared_base_template)
        self.assertIn('class="oldman-page-container"', shared_base_template)
        self.assertLess(
            shared_shell_template.index('id="oldman-sidebar-nav"'),
            shared_shell_template.index('id="oldman-main"'),
        )
        self.assertNotIn('data-turbo-frame="oldman-main"', sidebar_template)
        self.assertNotIn("turbo-cache-control", base_template)
        self.assertIn('{% include "oldman/dashboard/partials/preloader_critical_css.html" %}', shared_base_template)
        self.assertIn('{% include "oldman/dashboard/partials/preloader.html" %}', shared_base_template)
        self.assertIn('data-om-component="preloader"', partial)
        self.assertNotIn("data-turbo-temporary", partial)
        self.assertIn("data-om-preloader-status", partial)
        self.assertIn('<style data-om-critical="preloader">', critical_css)
        self.assertIn("#preloader", critical_css)
        self.assertIn("position: fixed", critical_css)
        self.assertIn("inset: 0", critical_css)
        self.assertIn("[data-om-preloader-status]", critical_css)
        self.assertIn("@keyframes oldman-preloader-spin", critical_css)

    def test_templates_expose_oldman_asset_base_before_main_entry(self) -> None:
        """模板必须在前端主入口执行前暴露静态资源基础地址。"""
        base_template = (settings.web.template.dir / "base.html").read_text(encoding="utf-8")

        self.assertIn('name="oldman-asset-base"', base_template)
        self.assertIn('bundle_asset_base_url(app_main_bundle)', base_template)
        self.assertLess(base_template.index('name="oldman-asset-base"'), base_template.index("bundle_entry(app_main_bundle"))

    def test_templates_use_static_bundle_helpers(self) -> None:
        """模板不得继续绑定单一 Vite manifest helper。"""
        base_template = (settings.web.template.dir / "base.html").read_text(encoding="utf-8")
        topbar_template = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")
        shared_language_template = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "language_switcher.html"
        ).read_text(encoding="utf-8")
        combined = "\n".join(
            [base_template, topbar_template, shared_language_template]
        )

        # The shell base renders the whole entry through the framework helper (client, preload, CSS, script in order).
        self.assertIn("bundle_entry(app_main_bundle, include_dev_client=true)", base_template)
        self.assertIn("bundle_asset_url(app_main_bundle", topbar_template)
        self.assertIn('include "oldman/dashboard/partials/language_switcher.html"', topbar_template)
        # The shared switcher lists languages by name with a check mark; flags stay out of the mono system.
        self.assertIn("om-dropdown-check", shared_language_template)
        self.assertNotIn("flagUrl", shared_language_template)
        self.assertNotIn("vite_entry(", combined)
        self.assertNotIn("vite_asset_url(", combined)
        self.assertNotIn("vite_asset_base_url(", combined)
        self.assertNotIn("/static/dist", combined)

    def test_notifications_template_uses_real_notification_center_components(self) -> None:
        """Notifications 页面必须用真实通知中心组件覆盖 topbar/table/modal/feedback。"""
        notifications_index = (settings.web.template.dir / "pages" / "notifications" / "index.html").read_text(encoding="utf-8")
        topbar_source = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")
        sidebar_source = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")
        epg_views_source = Path("apps/epg_admin/views.py").read_text(encoding="utf-8")

        self.assertIn("notification_stats.pending", notifications_index)
        self.assertEqual(notifications_index.count("om-avatar-sm"), 4)
        self.assertIn("notification_limit", notifications_index)
        self.assertIn("realtime notifications from existing business data", notifications_index)
        self.assertIn("filter_form.render", notifications_index)
        self.assertIn("table.render_shell", notifications_index)
        self.assertIn('id="notifications-feedback"', notifications_index)
        self.assertIn('"notification-detail-modal"', notifications_index)
        self.assertIn('managed=false', notifications_index)
        self.assertIn('data-notifications-clear-selected', notifications_index)
        self.assertIn("topbar_dashboard_notifications", self.app.ext.environment.globals)
        self.assertIn("topbar_dashboard_notifications()", topbar_source)
        self.assertIn('href="/notifications"', topbar_source)
        self.assertIn("/notifications", sidebar_source)
        self.assertIn("om-avatar-sm", epg_views_source)
        self.assertIn("min-w-0", epg_views_source)
        self.assertIn("break-words", epg_views_source)

    def test_topbar_partial_loads_notifications_from_global_helper(self) -> None:
        """未显式传通知 context 的后台页也必须由 topbar 全局 helper 渲染真实通知。"""
        import asyncio

        async def fake_notifications():
            """测试用异步 topbar 通知来源。"""
            return [
                {
                    "tone": "danger",
                    "icon": "ri-error-warning-line",
                    "title": "Global Topbar Notification",
                    "description": "Rendered from helper",
                    "time": None,
                    "href": "/notifications",
                }
            ]

        def render(user: RequestUser) -> str:
            request = SimpleNamespace(ctx=SimpleNamespace(user=user))
            return asyncio.run(self.app.ext.environment.get_template("partials/topbar.html").render_async(request=request))

        original = self.app.ext.environment.globals["topbar_dashboard_notifications"]
        self.app.ext.environment.globals["topbar_dashboard_notifications"] = fake_notifications
        try:
            html = render(RequestUser(id=1, username="tester", display_name="Tester", is_staff=True))
            member = render(RequestUser(id=2, username="member"))
        finally:
            self.app.ext.environment.globals["topbar_dashboard_notifications"] = original

        # Every signed-in account uses the EPG pages, so an account without staff sees the activity too.
        for page in (html, member):
            self.assertIn("Global Topbar Notification", page)
            self.assertIn('href="/notifications"', page)
        # Without a display name the account shows its username.
        self.assertIn('<span class="oldman-account-name">member</span>', member)

    def test_user_session_links_come_from_the_account_settings(self) -> None:
        """个人页是框架的(AccountFlow);菜单与顶栏只取 account_urls 给的地址。"""
        sidebar_source = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")
        topbar_source = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")

        self.assertIn("urls.profile", sidebar_source)
        self.assertIn("session_path=urls.profile", topbar_source)
        self.assertIn(
            'from "oldman/auth/partials/account_controls.html" import user_account_controls',
            topbar_source,
        )
        self.assertIn("user_account_controls(", topbar_source)

    def test_user_session_routes_are_registered(self) -> None:
        """AccountFlow 的会话页、密码弹窗和密码保存 endpoint 都已注册。"""
        route_names = set(self.app.router.name_index)

        self.assertIn(f"{self.app.name}.user_session", route_names)
        self.assertIn(f"{self.app.name}.user_session_password_modal", route_names)
        self.assertIn(f"{self.app.name}.user_session_password_submit", route_names)

    def test_user_session_password_form_can_target_session_endpoint(self) -> None:
        """Dashboard 与 Admin 必须渲染同一个框架密码表单片段。"""
        template = self.app.ext.environment.get_template(
            "oldman/auth/partials/password_form.html"
        )
        form = UserPasswordForm(request=SimpleNamespace(app=self.app), csrf_token="csrf-token")
        user = SimpleNamespace(id=7, username="session-admin", email="admin@example.com")

        import asyncio

        html = asyncio.run(template.render_async(user=user, form=form, action="/user-session/password"))

        self.assertIn('action="/user-session/password"', html)
        self.assertIn('data-om-component="form-validator"', html)
        self.assertIn('name="password"', html)
        self.assertIn('name="confirm_password"', html)
        self.assertFalse(
            (
                settings.web.template.dir
                / "partials"
                / "users"
                / "password_form.html"
            ).exists()
        )

    def test_backend_form_renderer_outputs_oldman_form_controls(self) -> None:
        """后端表单封装应该输出 Oldman/Tailwind 表单结构。"""
        form = ChannelsEpgForm(csrf_token="csrf-token")

        import asyncio

        html = str(asyncio.run(form.render(cancel_url="/channels-epg")))

        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn("novalidate", html)
        self.assertIn("om-form-grid", html)
        self.assertIn("om-field", html)
        # Boolean fields render as switch cards by default.
        self.assertIn("om-switch-card", html)
        self.assertIn('class="om-switch"', html)
        self.assertIn("om-button om-button-primary", html)

    def test_epg_list_form_outputs_remote_channel_select(self) -> None:
        """节目单表单的频道字段应该接入远程 Select provider。"""
        form = EpgListForm(
            request=SimpleNamespace(app=self.app),
            csrf_token="csrf-token",
            select_secret_key=SELECT_BINDING_SECRET,
        )

        import asyncio

        html = str(asyncio.run(form.render(cancel_url="/epg-list")))

        self.assertIn('name="channel_id"', html)
        self.assertIn('data-om-component="select"', html)
        self.assertIn('data-om-select-src="/admin/select/channels"', html)
        self.assertIn("data-om-select-bind", html)

    def test_epg_list_form_uses_the_framework_field_for_the_remote_channel_select(self) -> None:
        """简单的远程选择用框架的 AjaxSelectField（docs/developers/forms.md），它自带 AjaxSelectWidget。"""
        form = EpgListForm(select_secret_key=SELECT_BINDING_SECRET)

        self.assertIsInstance(form.channel_id, AjaxSelectField)
        self.assertIsInstance(form.channel_id.widget, AjaxSelectWidget)
        self.assertFalse(form.channel_id.validate_choice)

    def test_catalog_feed_form_outputs_remote_catalog_channel_select(self) -> None:
        """CatalogFeed 表单的频道身份字段应该接入远程 CatalogChannel provider。"""
        form = CatalogFeedForm(
            request=SimpleNamespace(app=self.app),
            csrf_token="csrf-token",
            select_secret_key=SELECT_BINDING_SECRET,
        )

        import asyncio

        html = str(asyncio.run(form.render(cancel_url="/catalog-feeds")))

        self.assertIn('name="catalog_channel_id"', html)
        self.assertIn('data-om-component="select"', html)
        self.assertIn('data-om-select-src="/admin/select/catalog_channels"', html)
        self.assertIn("data-om-select-bind", html)

    def test_upstream_record_filter_form_outputs_catalog_feed_autocomplete(self) -> None:
        """UpstreamRecord 筛选表单的 feed 字段应该接入远程 CatalogFeed autocomplete provider。"""
        form = UpstreamRecordFilterForm(
            request=SimpleNamespace(app=self.app),
            csrf_token="csrf-token",
            select_secret_key=SELECT_BINDING_SECRET,
        )

        import asyncio

        html = str(asyncio.run(form.render(method="get", table_target="#upstream-records-table")))

        self.assertIn('name="catalog_feed_id"', html)
        self.assertIn('data-om-component="autocomplete"', html)
        self.assertIn('data-om-select-src="/admin/select/catalog_feeds"', html)
        self.assertIn("data-om-select-bind", html)

    def test_logo_asset_filter_form_outputs_catalog_feed_autocomplete(self) -> None:
        """LogoAsset 筛选表单的 feed 字段应该接入远程 CatalogFeed autocomplete provider。"""
        form = LogoAssetFilterForm(
            request=SimpleNamespace(app=self.app),
            csrf_token="csrf-token",
            select_secret_key=SELECT_BINDING_SECRET,
        )

        import asyncio

        html = str(asyncio.run(form.render(method="get", table_target="#logo-assets-table")))

        self.assertIn('name="catalog_feed_id"', html)
        self.assertIn('name="quality_min"', html)
        self.assertIn('name="quality_max"', html)
        self.assertIn('data-om-component="autocomplete"', html)
        self.assertIn('data-om-select-src="/admin/select/catalog_feeds"', html)

    def test_match_decision_filter_form_outputs_relation_autocomplete(self) -> None:
        """MatchDecision 筛选表单应该接入 source/channel/feed 远程 autocomplete provider。"""
        form = MatchDecisionFilterForm(
            request=SimpleNamespace(app=self.app),
            csrf_token="csrf-token",
            select_secret_key=SELECT_BINDING_SECRET,
        )

        import asyncio

        html = str(asyncio.run(form.render(method="get", table_target="#match-decisions-table")))

        self.assertIn('name="decided_by"', html)
        self.assertIn('name="source_record_id"', html)
        self.assertIn('name="catalog_channel_id"', html)
        self.assertIn('name="catalog_feed_id"', html)
        self.assertIn('data-om-select-src="/admin/select/upstream_records"', html)
        self.assertIn('data-om-select-src="/admin/select/catalog_channels"', html)
        self.assertIn('data-om-select-src="/admin/select/catalog_feeds"', html)

    def test_notification_filter_form_outputs_type_severity_and_time_filters(self) -> None:
        """通知中心筛选表单应该覆盖类型、严重程度和时间范围。"""
        form = NotificationFilterForm(request=SimpleNamespace(app=self.app), csrf_token="csrf-token")

        import asyncio

        html = str(asyncio.run(form.render(method="get", table_target="#notifications-table")))

        self.assertIn('name="notification_type"', html)
        self.assertIn('name="severity"', html)
        self.assertIn('name="created_from"', html)
        self.assertIn('name="created_to"', html)
        self.assertIn('data-om-component="table-filter-form"', html)
        self.assertIn('data-om-table-target="#notifications-table"', html)

    def test_notifications_routes_and_table_are_registered(self) -> None:
        """通知中心页面、表格 endpoint 和详情弹窗路由必须注册。"""
        route_names = set(self.app.router.name_index)

        self.assertIn(f"{self.app.name}.notifications", route_names)
        self.assertIn(f"{self.app.name}.notifications_table", route_names)
        self.assertIn(f"{self.app.name}.notification_detail_modal", route_names)

    def test_notification_detail_missing_item_returns_displayable_modal_parts(self) -> None:
        """实时通知消失时详情接口也必须返回可展示 modal 内容，而不是让前端吃 404。"""
        import asyncio

        from apps.epg_admin import views as epg_views

        with patch.object(epg_views, "find_notification_item", new=AsyncMock(return_value=None)):
            handler = cast(Any, epg_views.notification_detail_modal)
            response = asyncio.run(handler.__wrapped__(SimpleNamespace(), "decision:missing"))

        payload = json.loads(response_body(response))
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["title"], "Notification Detail")
        # 协议上 html 和 body 是同一个容器；这里走框架的 modal_not_found_response，它写 html。
        self.assertIn("Notification is no longer available.", payload["html"])

    def test_notification_detail_lookup_uses_documented_window_limit(self) -> None:
        """通知详情查找必须复用通知中心窗口常量，避免页面和详情范围漂移。"""
        import asyncio

        from apps.epg_admin import services
        from apps.epg_admin import views as epg_views

        with patch.object(epg_views.services, "notification_items", new=AsyncMock(return_value=[])) as notification_items:
            item = asyncio.run(epg_views.find_notification_item("decision:missing"))

        self.assertIsNone(item)
        notification_items.assert_awaited_once_with(limit=services.NOTIFICATION_CENTER_LIMIT)

    def test_match_decisions_initial_filters_include_operator(self) -> None:
        """MatchDecision 列表首屏初始筛选必须把 operator 传给 table shell。"""
        source = (Path(__file__).resolve().parents[1] / "apps" / "epg_admin" / "views.py").read_text(encoding="utf-8")
        match_decisions_block = source[source.index('name="match_decisions"') : source.index('name="match_decisions_edit_modal"')]

        self.assertIn('"decided_by"', match_decisions_block)

    def test_form_accept_helpers_distinguish_json_and_html_modes(self) -> None:
        """业务表单响应应该能按 Accept 区分 JSON 与 HTML 片段模式。"""
        json_request = SimpleNamespace(headers={"accept": "application/json"})
        html_request = SimpleNamespace(headers={"accept": "text/html"})

        self.assertTrue(accepts_json_form_response(json_request))
        self.assertFalse(accepts_json_form_response(html_request))
        self.assertTrue(accepts_html_form_response(html_request))
        self.assertFalse(accepts_html_form_response(json_request))

    def test_json_form_error_response_uses_default_api_schema(self) -> None:
        """JSON 表单错误应该返回 DefaultApiFormResponse schema，而不是 HTML。"""
        request = SimpleNamespace(headers={"accept": "application/json"})
        form = ChannelsEpgForm(csrf_token="csrf-token")
        form.add_error("name", "Name is required")

        import asyncio

        response = asyncio.run(
            form_invalid_response(
                request,
                form,
                fragment=lambda: form.render(cancel_url="/channels-epg"),
                page=_page_must_not_render,
            )
        )
        payload = json.loads(response_body(response).decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["error_code"], 1100)
        self.assertEqual(payload["errors"]["name"], "Name is required")

    def test_html_form_error_response_returns_form_fragment(self) -> None:
        """HTML 表单错误模式应该返回局部表单片段，供前端替换目标容器。"""
        request = SimpleNamespace(headers={"accept": "text/html"})
        form = ChannelsEpgForm(csrf_token="csrf-token")
        form.add_error("name", "Name is required")

        import asyncio

        response = asyncio.run(
            form_invalid_response(
                request,
                form,
                fragment=lambda: form.render(cancel_url="/channels-epg"),
                page=_page_must_not_render,
            )
        )
        body = response_body(response).decode("utf-8")

        self.assertEqual(response.status, 422)
        self.assertIn("<form", body)
        self.assertIn("data-om-form", body)
        self.assertNotIn("page-content", body)

    def test_backend_table_renderer_outputs_oldman_table_structure(self) -> None:
        """后端表格封装应该输出 Oldman/Tailwind 表格结构。"""
        row = SimpleNamespace(id=1, name="Demo", src_url="https://example.test/epg.xml", country="US", hits=7, last_date="2026-06-08")
        table = ChannelsEpgTable()
        request = table.build_table_request(SimpleNamespace(args={}, headers={}), route_kwargs={})
        result = TableResult(rows=[row], row_contexts=[{}], total=1, filtered_total=1, page=1, page_size=20)

        import asyncio

        html = str(asyncio.run(table.render_html_fragment(request, result)))

        self.assertIn("data-om-table-partial", html)
        self.assertIn("om-table-shell", html)
        self.assertIn("om-table min-w-[680px]", html)
        self.assertIn("data-om-table-page-size-control", html)
        self.assertIn("data-om-table-pagination", html)
        self.assertIn("/channels-epg/1/edit", html)

    def test_epg_admin_routes_are_registered(self) -> None:
        """频道和节目单后台 CRUD 路由应该注册到 Sanic;用户管理是框架的 UserManagementFlow,这里只确认它装上了。"""
        route_names = set(self.app.router.name_index)

        for name in (
            "users",
            "users_table",
            "catalog_channels",
            "catalog_channels_table",
            "catalog_channels_new",
            "catalog_channels_edit",
            "catalog_feeds",
            "catalog_feeds_table",
            "catalog_feeds_new",
            "catalog_feeds_edit",
            "upstream_records",
            "upstream_records_table",
            "logo_assets",
            "logo_assets_table",
            "logo_asset_quality_distribution_chart",
            "logo_asset_mime_distribution_chart",
            "logo_asset_dimension_scatter_chart",
            "logo_assets_compare_modal",
            "match_decisions",
            "match_decisions_table",
            "match_decisions_edit_modal",
            "match_decisions_update",
            "channels_epg",
            "channels_epg_table",
            "channels_epg_new",
            "channels_epg_edit",
            "channel_names",
            "channel_names_table",
            "channel_names_new",
            "channel_names_edit",
            "admin_select_provider",
            "epg_list",
            "epg_list_table",
            "epg_list_new",
            "epg_list_edit",
        ):
            self.assertIn(f"{self.app.name}.{name}", route_names)
        self.assertNotIn(f"{self.app.name}.channels_select", route_names)

    def test_epg_admin_table_routes_can_be_reversed(self) -> None:
        """业务 Table data endpoint 应该可通过 route_name 反解。"""
        self.assertEqual(self.app.url_for("catalog_channels_table"), "/catalog-channels/table")
        self.assertEqual(self.app.url_for("catalog_feeds_table"), "/catalog-feeds/table")
        self.assertEqual(self.app.url_for("upstream_records_table"), "/upstream-records/table")
        self.assertEqual(self.app.url_for("logo_assets_table"), "/logo-assets/table")
        self.assertEqual(self.app.url_for("logo_asset_quality_distribution_chart"), "/logo-assets/charts/quality-distribution")
        self.assertEqual(self.app.url_for("logo_asset_mime_distribution_chart"), "/logo-assets/charts/mime-distribution")
        self.assertEqual(self.app.url_for("logo_asset_dimension_scatter_chart"), "/logo-assets/charts/dimensions")
        self.assertEqual(self.app.url_for("match_decisions_table"), "/match-decisions/table")
        self.assertEqual(self.app.url_for("users_table"), "/users/table")
        self.assertEqual(self.app.url_for("channels_epg_table"), "/channels-epg/table")
        self.assertEqual(self.app.url_for("channel_names_table"), "/channel-names/table")
        self.assertEqual(self.app.url_for("epg_list_table"), "/epg-list/table")

    def test_channels_select_provider_is_registered(self) -> None:
        """频道远程选择器 provider 应该在业务 import 链上静态注册。"""
        self.assertIsNotNone(select_registry.get("channels"))

    def test_channel_names_select_provider_and_sidebar_are_registered(self) -> None:
        """频道名称 provider 和侧边栏入口应该在业务 import 链上注册。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIsNotNone(select_registry.get("channel_names"))
        self.assertIn("/channel-names", sidebar)
        self.assertIn("channel_names", sidebar)

    def test_catalog_channels_sidebar_is_registered(self) -> None:
        """频道目录页面应该出现在 Catalog 侧边栏分组。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("/catalog-channels", sidebar)
        self.assertIn("catalog_channels", sidebar)

    def test_catalog_feeds_provider_and_sidebar_are_registered(self) -> None:
        """CatalogFeed 页面和频道目录 provider 应该在业务 import 链上注册。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIsNotNone(select_registry.get("catalog_channels"))
        self.assertIsNotNone(select_registry.get("catalog_feeds"))
        self.assertIn("/catalog-feeds", sidebar)
        self.assertIn("catalog_feeds", sidebar)

    def test_upstream_records_sidebar_is_registered(self) -> None:
        """上游记录审计页面应该出现在 Ingestion 侧边栏分组。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("/upstream-records", sidebar)
        self.assertIn("upstream_records", sidebar)

    def test_logo_assets_sidebar_is_registered(self) -> None:
        """Logo 资产质量工作台应该出现在 Ingestion 侧边栏分组。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("/logo-assets", sidebar)
        self.assertIn("logo_assets", sidebar)

    def test_match_decisions_sidebar_is_registered(self) -> None:
        """Match Decisions 人工审计页面应该出现在 Ingestion 侧边栏分组。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("/match-decisions", sidebar)
        self.assertIn("match_decisions", sidebar)

    def render_sidebar(self, user: RequestUser) -> str:
        request = SimpleNamespace(app=self.app, path="/dashboard", ctx=SimpleNamespace(user=user))
        template = self.app.ext.environment.get_template("partials/sidebar.html")
        return asyncio.run(template.render_async(request=request, active_section="dashboard", active_page="dashboard"))

    def test_the_menu_lists_every_page_and_keeps_the_admin_for_staff(self) -> None:
        """所有启用账户都能登录并使用全部 EPG 页面与示例;Admin 入口只给 staff(Admin 自己要求 staff)。"""
        staff = self.render_sidebar(RequestUser(id=1, username="admin", is_staff=True, is_superuser=True))
        member = self.render_sidebar(RequestUser(id=2, username="member"))

        for html in (staff, member):
            for href in ("/dashboard", settings.web.account.profile_url, "/channels-epg", "/catalog-channels", "/upstream-records", "/notifications", "/examples/"):
                self.assertIn(f'href="{href}', html)
        self.assertIn('href="/admin', staff)
        self.assertNotIn('href="/admin', member)
        # User management follows its permission, which a superuser holds and a member without roles does not.
        self.assertIn(f'href="{settings.web.account.users_url}"', staff)
        self.assertNotIn(f'href="{settings.web.account.users_url}"', member)


class WebServiceStartupChecksTest(unittest.TestCase):
    """启动前的两道检查：前端产物在，配置里的语言也都编译过。"""

    def test_prepare_server_refuses_a_missing_build_then_missing_catalogs(self) -> None:
        from oldman.web.staticfiles import StaticBundle, StaticBundleRegistry

        # 这里不建真 app：create_test_app() 一个进程只能跑一次（SSE 扩展会拒绝二次初始化），
        # 而 prepare_server 在这两道检查之前只用到 app.ctx 上的 registry。
        app = SimpleNamespace(ctx=SimpleNamespace())
        service = WebService(settings.core.app_name)
        with tempfile.TemporaryDirectory(prefix="oldman-epg-startup-") as directory:
            root = Path(directory)
            registry = StaticBundleRegistry()
            registry.register(
                StaticBundle(
                    name="app:main",
                    entry_path="src/main.ts",
                    manifest_path=root / "dist" / ".vite" / "manifest.json",
                    static_url="/static/dist",
                )
            )
            app.ctx.static_bundle_registry = registry

            # 没跑过 pnpm build：manifest 不在。
            with self.assertRaisesRegex(RuntimeError, "Vite manifest"):
                service.prepare_server(cast(Any, app))

            manifest = root / "dist" / ".vite" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"src/main.ts": {"file": "assets/main.js"}}), encoding="utf-8")

            # 产物在了，但语言包没发布：同样不许起。
            with self.assertRaisesRegex(RuntimeError, "Frontend catalogs"):
                service.prepare_server(cast(Any, app))


def create_test_app() -> Sanic:
    """创建独立的 WebService 应用，供路由测试使用。"""
    try:
        Sanic.unregister_app(Sanic.get_app(settings.core.app_name))
    except SanicException:
        pass

    return WebService(settings.core.app_name).create_app()


if __name__ == "__main__":
    unittest.main()
