"""Sanic Web 服务接入。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from apps.auth.session import DashboardSessionData
from apps.examples.http_example import http_client
from config.settings import settings

from oldman.db import db_manager
from oldman.i18n import LanguageRegistry, gettext, gettext_noop
from oldman.runtime.web import WebApplication
from oldman.web.i18n.assets import direct_flag_url
from oldman.web.messages.notifications import (
    NotificationRoutes,
)
from oldman.web.messages.notifications import (
    init_app as install_notifications,
)
from oldman.web.request import Request
from oldman.web.routing import WebApp
from oldman.web.security.csrf import StatelessCSRFManager
from oldman.web.session import SessionData
from oldman.web.staticfiles import StaticBundle, StaticBundleRegistry
from oldman.web.template import install_template_loaders

APP_MAIN_BUNDLE = "app:main"


def is_vite_dev_mode() -> bool:
    """判断模板是否应该从 Vite 开发服务器加载资源。"""
    explicit_dev = os.environ.get("OLDMAN_DEV")
    return explicit_dev is not None and explicit_dev.strip().lower() in {"1", "true", "yes", "on"}


def create_static_bundle_registry() -> StaticBundleRegistry:
    """创建当前项目的静态资源 bundle registry。"""
    dev_mode = is_vite_dev_mode()
    static_root = str(settings.web.static.root).strip()
    static_url = str(settings.web.static.url).strip()
    if not dev_mode and (not static_root or not static_url):
        raise RuntimeError(
            "Dashboard production assets require settings.web.static.root and "
            "settings.web.static.url; configure them and run "
            "`oldman web static collect` before startup"
        )

    manifest_root = Path(static_root) if static_root else Path()
    registry = StaticBundleRegistry()
    registry.register(
        StaticBundle(
            name=APP_MAIN_BUNDLE,
            entry_path="src/main.ts",
            manifest_path=manifest_root / "dist" / ".vite" / "manifest.json",
            static_url=f"{static_url.rstrip('/')}/dist" if static_url else "",
            dev_server_url=settings.web.frontend.vite_dev_server_url,
            dev_mode=dev_mode,
            passthrough_prefixes=("theme/",),
        )
    )
    return registry


def ensure_vite_build_available(entry_path: str = "src/main.ts") -> None:
    """产品模式启动前校验编译产物，避免服务输出无脚本页面。"""
    create_static_bundle_registry().ensure_build_available(APP_MAIN_BUNDLE, entry_path)


def install_template_helpers(
    app: WebApp,
    *,
    notification_routes: NotificationRoutes,
    user_events_url: str | None,
) -> None:
    """向 Sanic-Ext Jinja 环境注入 Oldman 模板辅助函数。"""
    from apps.epg_admin.services import dashboard_notifications

    bundle_registry = create_static_bundle_registry()
    app.ctx.static_bundle_registry = bundle_registry

    environment = app.ext.environment
    install_template_loaders(environment, settings.web.template.dir)
    environment.globals.update(
        _=gettext,
        app_main_bundle=APP_MAIN_BUNDLE,
        bundle_asset_base_url=bundle_registry.asset_base_url,
        bundle_asset_url=bundle_registry.asset_url,
        bundle_client=bundle_registry.client_tags,
        bundle_entry=bundle_registry.entry_tags,
        bundle_modulepreload=bundle_registry.modulepreload_tags,
        bundle_script=bundle_registry.script_tags,
        bundle_styles=bundle_registry.styles_tags,
        dashboard_csrf_token=dashboard_csrf_token,
        dashboard_current_language=current_dashboard_language,
        dashboard_language_items=dashboard_language_items,
        dashboard_user_events_url=user_events_url,
        dashboard_user_notification_urls={
            "center": notification_routes.center_url,
            "topbar": notification_routes.topbar_url,
        },
        gettext=gettext,
        topbar_dashboard_notifications=dashboard_notifications,
    )


def dashboard_language_registry() -> LanguageRegistry:
    """Return the canonical registry shared with the framework Web runtime."""
    return LanguageRegistry(settings.i18n.languages)


def normalize_dashboard_language(language: str) -> str:
    """把配置和请求里的语言代码统一成 dashboard 前端使用的规范代码。"""
    return dashboard_language_registry().resolve(language)


def dashboard_supported_languages() -> list[str]:
    """按配置顺序返回 dashboard 支持的规范语言代码。"""
    return list(dashboard_language_registry().codes)


def current_dashboard_language(request: Request | None = None) -> str:
    """返回当前请求语言，优先使用请求上下文，再回退到 cookie 和默认配置。"""
    cookies = getattr(request, "cookies", {}) if request else {}
    candidates = [
        str(getattr(getattr(request, "ctx", None), "locale", "") or "") if request else "",
        cookies.get("lang", "") if hasattr(cookies, "get") else "",
        cookies.get("preferred_language", "") if hasattr(cookies, "get") else "",
        settings.i18n.default_language,
    ]
    supported_languages = dashboard_supported_languages()
    supported = set(supported_languages)
    for candidate in candidates:
        normalized = normalize_dashboard_language(candidate)
        if normalized in supported:
            return normalized
    default_language = normalize_dashboard_language(settings.i18n.default_language)
    if default_language in supported:
        return default_language
    return supported_languages[0] if supported_languages else "en"


def dashboard_language_items(request: Request | None = None) -> list[dict[str, object]]:
    """生成 dashboard 顶栏语言菜单数据，不携带站点跳转 URL。"""
    current = current_dashboard_language(request)
    items: list[dict[str, object]] = []
    for definition in dashboard_language_registry():
        asset_path = definition.flag
        flag_url = direct_flag_url(asset_path, static_url=settings.web.static.url)
        items.append(
            {
                "code": definition.code,
                "aliases": list(definition.aliases),
                "flag": asset_path,
                "flag_asset": flag_url,
                "flagUrl": flag_url,
                "is_current": definition.code == current,
                "locale": definition.code,
                "name": definition.name,
            }
        )
    return items


def dashboard_csrf_token(request: Request | None = None) -> str:
    """生成页面级 CSRF token，供没有表单的后台页面发起安全 POST。"""
    if request is None:
        return ""
    csrf_manager = getattr(request.app.ctx, "csrf", None)
    if csrf_manager is None:
        return ""
    return str(csrf_manager.generate_token(request))


class WebService(WebApplication):
    """Oldman 后台 Web 服务。"""

    SERVICE_NAME = "Oldman Web 服务"
    SESSION_MODEL: ClassVar[type[SessionData]] = DashboardSessionData
    USE_I18N = True

    @classmethod
    def get_default_commands(cls) -> dict[str, tuple[Callable[..., Any], str]]:
        """返回 Web 服务命令，默认 start 为产品模式，dev 显式开启 Vite。"""
        commands = super().get_default_commands()
        commands["dev"] = (
            cls.dev,
            gettext_noop(
                "Start the development service with frontend assets from Vite."
            ),
        )
        return commands

    def dev(self, *args: Any, **kwargs: Any) -> None:
        """启动开发模式服务。"""
        os.environ["OLDMAN_DEV"] = "1"
        self.start(*args, **kwargs)

    def get_ext_config(self) -> dict[str, Any]:
        """返回 Sanic-Ext 配置。"""
        return {
            "oas": False,
            "oas_autodoc": False,
            "templating_path_to_templates": settings.web.template.dir,
            "templating_enable_async": True,
            "logging": False,
            "cors": True,
        }

    def init(self) -> None:
        """初始化 Sanic、Session、CSRF 与模板辅助函数。"""
        super().init()
        app = self.runtime_app
        if app is None:
            raise RuntimeError("Sanic app was not initialized")

        StatelessCSRFManager(app)
        notification_routes = install_notifications(app)
        install_template_helpers(
            app,
            notification_routes=notification_routes,
            user_events_url="/user-events" if settings.web.sse.enabled else None,
        )

    async def before_server_start(self, app: WebApp) -> None:
        """Initialize the example HTTP adapter without connecting to its upstream."""
        await super().before_server_start(app)
        await http_client.init_client()

    async def before_server_stop(self, app: WebApp) -> None:
        """服务停止前关闭数据库连接池。"""
        await super().before_server_stop(app)
        await db_manager.close()

    async def after_server_stop(self, app: WebApp) -> None:
        """Close the HTTP pool and always preserve the framework's own cleanup."""
        try:
            await http_client.close_client()
        finally:
            await super().after_server_stop(app)

    def prepare_server(self, app: WebApp) -> None:
        """按照项目配置准备 Sanic 监听参数。"""
        ensure_vite_build_available()
        app.prepare(
            host=settings.web.listen_host,
            port=settings.web.listen_port,
            debug=settings.web.debug,
            motd=False,
            auto_reload=settings.web.auto_reload,
            single_process=True,
            workers=settings.web.workers,
            access_log=settings.web.access_log,
        )
