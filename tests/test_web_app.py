"""Sanic Web 应用工厂测试。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from apps.auth.session import DashboardSessionData
from apps.epg_admin.form_responses import accepts_html_form_response, accepts_json_form_response, form_error_response
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
    APP_MAIN_BUNDLE,
    WebService,
    create_static_bundle_registry,
    ensure_vite_build_available,
    install_template_helpers,
    is_vite_dev_mode,
)

from oldman.runtime.discovery import (
    discover_service_definitions,
    load_service_class,
)
from oldman.web.auth import UserPasswordForm, UserSessionProfile
from oldman.web.components.forms import AjaxSelectField, AjaxSelectWidget
from oldman.web.components.selects import select_registry
from oldman.web.components.tables import TableResult
from oldman.web.package_data import package_template_dir
from oldman.web.security import WebSecurityPurpose, configured_web_security_key
from oldman.web.staticfiles import StaticBundle, StaticBundleRegistry

SELECT_BINDING_SECRET = configured_web_security_key(
    WebSecurityPurpose.SELECT_BINDING
)
ROOT = Path(__file__).resolve().parents[1]


def response_body(response: object) -> bytes:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise AssertionError("response has no byte body")
    return body


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
        from oldman.web.messages.notifications import NotificationRoutes

        environment = self.app.ext.environment
        token = current_translations.set(None)
        try:
            # 初始化 helper 会同时修改共享模板变量和资源注册表，失败时也要恢复。
            with (
                patch.object(settings.i18n, "use_i18n", False),
                patch.dict(environment.globals),
                patch.object(self.app.ctx, "static_bundle_registry", self.app.ctx.static_bundle_registry),
            ):
                install_template_helpers(
                    self.app,
                    notification_routes=NotificationRoutes(
                        topbar_url="/notifications/topbar",
                        read_url="/notifications/read",
                        delete_url="/notifications/delete",
                        center_url="/notifications",
                    ),
                    user_events_url=None,
                )
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

    def test_web_start_defaults_to_built_assets(self) -> None:
        """未显式开启开发模式时，后台服务应该默认使用编译产物。"""
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(is_vite_dev_mode())

    def test_oldman_env_does_not_enable_vite_dev_mode(self) -> None:
        """环境名不能让正式启动命令隐式走 Vite。"""
        with patch.dict("os.environ", {"OLDMAN_ENV": "development"}, clear=True):
            self.assertFalse(is_vite_dev_mode())

    def test_oldman_dev_enables_vite_dev_mode(self) -> None:
        """只有显式 OLDMAN_DEV 开关才允许模板加载 Vite 开发服务器。"""
        with patch.dict("os.environ", {"OLDMAN_DEV": "1"}, clear=True):
            self.assertTrue(is_vite_dev_mode())

    def test_dashboard_manifest_is_read_from_collected_static_root(self) -> None:
        """生产 manifest 必须来自公开收集目录，而不是项目源码目录。"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            collected_root = Path(temporary_directory) / "public"
            source_root = Path(temporary_directory) / "source"
            static_settings = SimpleNamespace(
                dir=source_root,
                root=collected_root,
                url="/static",
            )
            frontend_settings = SimpleNamespace(
                vite_dev_server_url="http://127.0.0.1:5173",
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch(
                    "services.web.settings",
                    SimpleNamespace(
                        web=SimpleNamespace(
                            frontend=frontend_settings,
                            static=static_settings,
                        ),
                    ),
                ),
            ):
                bundle = create_static_bundle_registry().get(APP_MAIN_BUNDLE)

        self.assertEqual(
            collected_root / "dist" / ".vite" / "manifest.json",
            bundle.manifest_path,
        )

    def test_dashboard_production_rejects_empty_static_config(self) -> None:
        """生产模式不能把空静态配置隐式退化为当前目录和 /dist。"""
        runtime_settings = SimpleNamespace(
            web=SimpleNamespace(
                frontend=SimpleNamespace(
                    vite_dev_server_url="http://127.0.0.1:5173",
                ),
                static=SimpleNamespace(root="", url=""),
            ),
        )

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("services.web.settings", runtime_settings),
            self.assertRaisesRegex(RuntimeError, "settings.web.static.root"),
        ):
            create_static_bundle_registry()

    def test_dashboard_dev_mode_allows_empty_production_static_config(
        self,
    ) -> None:
        """Vite 开发模式不依赖尚未配置的生产静态目录。"""
        runtime_settings = SimpleNamespace(
            web=SimpleNamespace(
                frontend=SimpleNamespace(
                    vite_dev_server_url="http://127.0.0.1:5173",
                ),
                static=SimpleNamespace(root="", url=""),
            ),
        )

        with (
            patch.dict("os.environ", {"OLDMAN_DEV": "1"}, clear=True),
            patch("services.web.settings", runtime_settings),
        ):
            bundle = create_static_bundle_registry().get(APP_MAIN_BUNDLE)

        self.assertTrue(bundle.dev_mode)
        self.assertEqual("", bundle.static_url)

    def test_web_service_exposes_dev_command(self) -> None:
        """web 服务应该提供显式开发模式命令。"""
        commands = WebService.get_default_commands()

        self.assertIn("dev", commands)
        self.assertIn("development service", commands["dev"][1])

    def test_product_mode_requires_built_static_bundle_manifest(self) -> None:
        """产品模式缺少静态 bundle manifest 时必须硬失败，不能渲染无脚本页面。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = StaticBundleRegistry()
            registry.register(
                StaticBundle(
                    name=APP_MAIN_BUNDLE,
                    entry_path="src/main.ts",
                    manifest_path=Path(tmp_dir) / "dist" / ".vite" / "manifest.json",
                    static_url="/static/dist",
                )
            )
            with patch.dict("os.environ", {}, clear=True), patch("services.web.create_static_bundle_registry", return_value=registry):
                with self.assertRaisesRegex(RuntimeError, "pnpm build"):
                    ensure_vite_build_available()

    def test_product_mode_requires_declared_static_bundle_entry(self) -> None:
        """产品模式 manifest 缺少 bundle 主入口时必须硬失败，不能静默输出空标签。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest_path = Path(tmp_dir) / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            registry = StaticBundleRegistry()
            registry.register(
                StaticBundle(
                    name=APP_MAIN_BUNDLE,
                    entry_path="src/main.ts",
                    manifest_path=manifest_path,
                    static_url="/static/dist",
                )
            )
            with patch.dict("os.environ", {}, clear=True), patch("services.web.create_static_bundle_registry", return_value=registry):
                with self.assertRaisesRegex(RuntimeError, "src/main.ts"):
                    ensure_vite_build_available()

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
        self.assertIn('href="/dashboard"', sidebar_source)
        self.assertIn('href="/dashboard/analytics"', sidebar_source)
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
        language_source = (settings.web.template.dir / "partials" / "language_switcher.html").read_text(encoding="utf-8")
        shared_language_source = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "language_switcher.html"
        ).read_text(encoding="utf-8")

        self.assertIn("partials/language_switcher.html", topbar_source)
        self.assertIn(
            'oldman/dashboard/partials/language_switcher.html',
            language_source,
        )
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
        self.assertIn("Use your administrator account to continue", response.text)
        self.assertIn('name="next" value="/"', response.text)

    def test_login_response_renders_tailwind_auth_shell(self) -> None:
        """登录页应该渲染当前 Tailwind 登录结构。"""
        _request, response = self.app.test_client.get("/login")

        self.assertEqual(response.status, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('<link rel="icon" href="data:,">', response.text)
        self.assertIn('data-om-page="login"', response.text)
        self.assertIn("oldman-brand-mark", response.text)
        self.assertIn("om-analytics-tile", response.text)
        self.assertIn("EPG management dashboard", response.text)
        self.assertIn('action="/login"', response.text)
        self.assertIn('name="csrfmiddlewaretoken"', response.text)
        self.assertIn('name="username"', response.text)
        self.assertIn('name="password"', response.text)

    def test_login_response_uses_the_request_language(self) -> None:
        """服务端 HTML language 必须来自统一请求语言，而不是模板 fallback。"""
        _request, response = self.app.test_client.get(
            "/login",
            headers={"Cookie": "lang=zh-hans; preferred_language=zh-hans"},
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
        self.assertIn('href="/catalog-channels" class="om-button om-button-soft-secondary om-button-sm"', catalog_channels_form)
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
        """基础后台页和登录页都应该挂载标准 preloader 组件。"""
        base_template = (settings.web.template.dir / "base.html").read_text(encoding="utf-8")
        shared_base_template = (package_template_dir() / "oldman" / "dashboard" / "base.html").read_text(encoding="utf-8")
        shared_shell_template = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "shell.html"
        ).read_text(encoding="utf-8")
        login_template = (settings.web.template.dir / "pages" / "login.html").read_text(encoding="utf-8")
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
        self.assertIn('data-preloader="enable"', login_template)
        self.assertIn('<meta name="turbo-visit-control" content="reload">', login_template)
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
        self.assertNotIn('id="oldman-main"', login_template)
        self.assertNotIn("turbo-cache-control", base_template)
        self.assertIn('{% include "oldman/dashboard/partials/preloader_critical_css.html" %}', shared_base_template)
        self.assertIn('{% include "oldman/dashboard/partials/preloader_critical_css.html" %}', login_template)
        self.assertLess(
            login_template.index('oldman/dashboard/partials/preloader_critical_css.html'),
            login_template.index("bundle_client(app_main_bundle)"),
        )
        self.assertLess(
            login_template.index('oldman/dashboard/partials/preloader_critical_css.html'),
            login_template.index("bundle_script(app_main_bundle)"),
        )
        self.assertIn('{% include "oldman/dashboard/partials/preloader.html" %}', shared_base_template)
        self.assertIn('{% include "oldman/dashboard/partials/preloader.html" %}', login_template)
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
        login_template = (settings.web.template.dir / "pages" / "login.html").read_text(encoding="utf-8")

        self.assertIn('name="oldman-asset-base"', base_template)
        self.assertIn('name="oldman-asset-base"', login_template)
        self.assertIn('bundle_asset_base_url(app_main_bundle)', base_template)
        self.assertIn('bundle_asset_base_url(app_main_bundle)', login_template)
        self.assertLess(base_template.index('name="oldman-asset-base"'), base_template.index("bundle_script(app_main_bundle)"))
        self.assertLess(login_template.index('name="oldman-asset-base"'), login_template.index("bundle_script(app_main_bundle)"))

    def test_templates_use_static_bundle_helpers(self) -> None:
        """模板不得继续绑定单一 Vite manifest helper。"""
        base_template = (settings.web.template.dir / "base.html").read_text(encoding="utf-8")
        login_template = (settings.web.template.dir / "pages" / "login.html").read_text(encoding="utf-8")
        topbar_template = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")
        language_template = (settings.web.template.dir / "partials" / "language_switcher.html").read_text(encoding="utf-8")
        shared_language_template = (
            package_template_dir()
            / "oldman"
            / "dashboard"
            / "partials"
            / "language_switcher.html"
        ).read_text(encoding="utf-8")
        combined = "\n".join(
            [base_template, login_template, topbar_template, language_template, shared_language_template]
        )

        self.assertIn("bundle_styles(app_main_bundle)", base_template)
        self.assertIn("bundle_modulepreload(app_main_bundle)", base_template)
        self.assertIn("bundle_script(app_main_bundle)", base_template)
        for template in (base_template, login_template):
            self.assertLess(template.index("bundle_modulepreload(app_main_bundle)"), template.index("bundle_styles(app_main_bundle)"))
            self.assertLess(template.index("bundle_styles(app_main_bundle)"), template.index("bundle_script(app_main_bundle)"))
        self.assertIn("bundle_asset_url(app_main_bundle", topbar_template)
        self.assertIn('include "oldman/dashboard/partials/language_switcher.html"', language_template)
        self.assertIn("language.flagUrl", shared_language_template)
        self.assertNotIn("vite_entry(", combined)
        self.assertNotIn("vite_asset_url(", combined)
        self.assertNotIn("vite_asset_base_url(", combined)
        self.assertNotIn("/static/dist", combined)

    def test_users_template_uses_real_user_management_components(self) -> None:
        """Users 页面必须是真实 OldmanUser 管理页，不再是 dashboard 临时弹窗。"""
        users_index = (settings.web.template.dir / "pages" / "users" / "index.html").read_text(encoding="utf-8")
        user_form = (settings.web.template.dir / "pages" / "users" / "form.html").read_text(encoding="utf-8")
        password_form = (
            package_template_dir()
            / "oldman"
            / "auth"
            / "partials"
            / "password_form.html"
        ).read_text(encoding="utf-8")
        base_page_source = Path("frontend/src/pages/base-page.ts").read_text(encoding="utf-8")

        self.assertIn("filter_form.render", users_index)
        self.assertIn("table.render_shell", users_index)
        self.assertIn('id="users-feedback"', users_index)
        self.assertIn('"user-password-modal"', users_index)
        self.assertIn('component="modal"', users_index)
        self.assertIn("remote_content=true", users_index)
        self.assertNotIn('data-om-modal-close>{{ _("Close") }}</button>', users_index)
        self.assertIn("form.render", user_form)
        self.assertIn("validate=True", user_form)
        self.assertIn('form_mode="json"', user_form)
        self.assertIn("users-form-feedback", user_form)
        self.assertIn("feedback_target", user_form)
        self.assertIn('data-om-component="form-validator"', password_form)
        self.assertIn('name="confirm_password"', password_form)
        self.assertFalse(Path("frontend/src/components/user-edit-modal.ts").exists())
        self.assertNotIn("user-edit-modal", base_page_source)
        self.assertNotIn("UserEditModal", base_page_source)

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

        original = self.app.ext.environment.globals["topbar_dashboard_notifications"]
        self.app.ext.environment.globals["topbar_dashboard_notifications"] = fake_notifications
        request = SimpleNamespace(ctx=SimpleNamespace(session=DashboardSessionData(display_name="Tester")))
        try:
            html = asyncio.run(self.app.ext.environment.get_template("partials/topbar.html").render_async(request=request))
        finally:
            self.app.ext.environment.globals["topbar_dashboard_notifications"] = original

        self.assertIn("Global Topbar Notification", html)
        self.assertIn('href="/notifications"', html)

    def test_user_session_template_uses_current_session_components(self) -> None:
        """项目页面和顶栏只能为框架共享 Session 组件提供外壳与路径。"""
        session_index = (settings.web.template.dir / "pages" / "user_session" / "index.html").read_text(encoding="utf-8")
        sidebar_source = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")
        topbar_source = (settings.web.template.dir / "partials" / "topbar.html").read_text(encoding="utf-8")

        self.assertIn(
            '{% include "oldman/auth/user_session_content.html" %}',
            session_index,
        )
        self.assertNotIn("current_user", session_index)
        self.assertNotIn("session_data", session_index)
        self.assertIn("/user-session", sidebar_source)
        self.assertIn(
            'from "oldman/auth/partials/account_controls.html" import user_account_controls',
            topbar_source,
        )
        self.assertIn("user_account_controls(", topbar_source)

    def test_user_session_page_reads_the_typed_snapshot_without_querying_user(self) -> None:
        """会话展示是 Session 热路径，只有修改密码时才允许读取数据库。"""
        import asyncio

        from apps.auth import views as auth_views

        request = SimpleNamespace(
            app=self.app,
            args={},
            cookies={},
            ctx=SimpleNamespace(
                session=DashboardSessionData(
                    user_id=7,
                    username="alice",
                    display_name="Alice",
                    login_ip="127.0.0.1",
                    login_time=1_690_000_000,
                    is_active=True,
                    is_staff=True,
                )
            ),
            form={},
            headers={"accept": "text/html"},
            method="GET",
            path="/user-session",
            query_string="",
        )
        rendered = object()
        with (
            patch.object(
                auth_views,
                "get_user_by_id",
                new=AsyncMock(return_value=SimpleNamespace(username="database-user")),
            ) as get_user,
            patch.object(
                auth_views,
                "render_template",
                new=AsyncMock(return_value=rendered),
            ) as render_template,
        ):
            response = asyncio.run(cast(Any, auth_views.user_session)(request))

        self.assertIs(rendered, response)
        get_user.assert_not_awaited()
        render_call = render_template.await_args
        assert render_call is not None
        context = render_call.kwargs["context"]
        self.assertIsInstance(context["session_profile"], UserSessionProfile)
        self.assertEqual("Alice", context["session_profile"].display_name)
        self.assertEqual(
            "/user-session/password-modal",
            context["password_modal_path"],
        )

    def test_user_session_routes_are_registered(self) -> None:
        """会话页、远程密码弹窗和当前用户密码保存 endpoint 必须注册。"""
        route_names = set(self.app.router.name_index)

        self.assertIn(f"{self.app.name}.user_session", route_names)
        self.assertIn(f"{self.app.name}.user_session_password_modal", route_names)
        self.assertIn(f"{self.app.name}.user_session_password_update", route_names)

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
        self.assertIn("om-check", html)
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

    def test_epg_list_form_uses_widget_for_remote_channel_select(self) -> None:
        """业务远程选择器应该优先使用 widget，而不是把 provider 逻辑塞进 Field。"""
        form = EpgListForm(select_secret_key=SELECT_BINDING_SECRET)

        self.assertNotIsInstance(form.channel_id, AjaxSelectField)
        self.assertIsInstance(form.channel_id.widget, AjaxSelectWidget)

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
        self.assertIn("Notification is no longer available.", payload["body"])

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
            form_error_response(
                request,
                form,
                template="pages/channels_epg/form.html",
                context={"form": form},
                cancel_url="/channels-epg",
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
            form_error_response(
                request,
                form,
                template="pages/channels_epg/form.html",
                context={"form": form},
                cancel_url="/channels-epg",
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
        """频道和节目单后台 CRUD 路由应该注册到 Sanic。"""
        route_names = set(self.app.router.name_index)

        for name in (
            "users",
            "users_table",
            "users_new",
            "users_edit_page",
            "users_update",
            "users_password_modal",
            "users_password_update",
            "users_status_modal",
            "users_status_update",
            "users_delete_modal",
            "users_delete",
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

    def test_users_sidebar_is_registered(self) -> None:
        """Users 用户管理页面应该出现在 System 侧边栏分组。"""
        sidebar = (settings.web.template.dir / "partials" / "sidebar.html").read_text(encoding="utf-8")

        self.assertIn("/users", sidebar)
        self.assertIn("users", sidebar)
        self.assertIn("System", sidebar)


def create_test_app() -> Sanic:
    """创建独立的 WebService 应用，供路由测试使用。"""
    try:
        Sanic.unregister_app(Sanic.get_app(settings.core.app_name))
    except SanicException:
        pass

    return WebService(settings.core.app_name).create_app()


if __name__ == "__main__":
    unittest.main()
