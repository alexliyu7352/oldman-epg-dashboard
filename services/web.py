"""Sanic Web 服务接入。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from oldman.apps.admin import install_admin
from oldman.i18n import gettext, gettext_noop
from oldman.runtime.web import WebApplication
from oldman.web.i18n import ensure_frontend_catalogs
from oldman.web.messages.notifications import (
    NotificationRoutes,
)
from oldman.web.messages.notifications import (
    init_app as install_notifications,
)
from oldman.web.routing import WebApp
from oldman.web.security.csrf import StatelessCSRFManager
from oldman.web.session import SessionData
from oldman.web.staticfiles import DEV_MODE_ENV, app_bundle_registry, dev_mode_requested, register_project_bundle
from oldman.web.template import install_template_loaders

from apps.auth.session import DashboardSessionData
from apps.examples.http_example import http_client
from config.settings import settings

APP_MAIN_BUNDLE = "app:main"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def install_template_helpers(
    app: WebApp,
    *,
    notification_routes: NotificationRoutes,
    user_events_url: str | None,
) -> None:
    """向 Sanic-Ext Jinja 环境注入 Oldman 模板辅助函数。"""
    from apps.epg_admin.services import dashboard_notifications

    bundle_registry = app_bundle_registry(app)
    register_project_bundle(
        bundle_registry,
        name=APP_MAIN_BUNDLE,
        entry_path="src/main.ts",
        static_root=settings.web.static.root,
        static_url=settings.web.static.url,
        dev_mode=dev_mode_requested(),
        dev_server_url=settings.web.frontend.vite_dev_server_url,
        passthrough_prefixes=("theme/",),
    )

    environment = app.ext.environment
    install_template_loaders(environment, settings.web.template.dir)
    bundle_registry.install_template_globals(environment)
    environment.globals.update(
        _=gettext,
        app_main_bundle=APP_MAIN_BUNDLE,
        dashboard_user_events_url=user_events_url,
        dashboard_user_notification_urls={
            "center": notification_routes.center_url,
            "topbar": notification_routes.topbar_url,
        },
        gettext=gettext,
        topbar_dashboard_notifications=dashboard_notifications,
    )


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
        os.environ[DEV_MODE_ENV] = "1"
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
        # 内置管理后台挂在 /admin:用户和角色在这里管理。它有自己的布局和前端资源,
        # 与这里共用登录状态,导航里的入口整页打开。
        install_admin(app)

    async def before_server_start(self, app: WebApp) -> None:
        """Initialize the example HTTP adapter without connecting to its upstream."""
        await super().before_server_start(app)
        await http_client.init_client()

    async def after_server_stop(self, app: WebApp) -> None:
        """Close the HTTP pool and always preserve the framework's own cleanup."""
        try:
            await http_client.close_client()
        finally:
            await super().after_server_stop(app)

    def prepare_server(self, app: WebApp) -> None:
        """先确认前端产物可用，再交给框架按配置准备监听参数。"""
        registry = app_bundle_registry(app)
        registry.ensure_build_available(APP_MAIN_BUNDLE)
        # 切换器里的语言来自配置，词典来自前端构建：少一份就会点出一个没有翻译的语言。
        ensure_frontend_catalogs(registry, APP_MAIN_BUNDLE, source_dir=PROJECT_ROOT / "frontend" / "public" / "i18n")
        super().prepare_server(app)
