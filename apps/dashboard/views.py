"""后台首页视图。"""

from __future__ import annotations

from apps.auth.decorators import admin_required
from oldman.web.request import Request
from oldman.web.routing import get_app
from oldman.web.template import render_template

from .chart_views import DashboardFeedStatusChart, DashboardLogoQualityChart, DashboardProgrammeTrendChart
from .services import dashboard_notifications, dashboard_stats

app = get_app()


def dashboard_chart_context(request: Request) -> dict[str, object]:
    """构造 Dashboard 图表实例上下文。"""
    return {
        "programme_trend_chart": DashboardProgrammeTrendChart(request=request),
        "feed_status_chart": DashboardFeedStatusChart(request=request),
        "logo_quality_chart": DashboardLogoQualityChart(request=request),
    }


async def dashboard_page_context(request: Request, active_page: str) -> dict[str, object]:
    """构造 Dashboard 页面共享上下文。"""
    return {
        "active_page": active_page,
        "active_section": "dashboard",
        "dashboard_notifications": await dashboard_notifications(),
        "stats": await dashboard_stats(),
        **dashboard_chart_context(request),
    }


@app.get("/", name="dashboard")
@app.get("/dashboard", name="dashboard_alias")
@admin_required()
async def dashboard(request: Request):
    """渲染真实业务统计首页。"""
    return await render_template("pages/dashboard.html", context=await dashboard_page_context(request, "dashboard_overview"))


@app.get("/dashboard/analytics", name="dashboard_analytics")
@admin_required()
async def dashboard_analytics(request: Request):
    """渲染 Dashboard 分析图表页。"""
    return await render_template("pages/dashboard_analytics.html", context=await dashboard_page_context(request, "dashboard_analytics"))
