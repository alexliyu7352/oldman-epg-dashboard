"""Dashboard 图表 data endpoint。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import case, func, select

from apps.epg_admin.models import CatalogFeed, CatalogLogoAsset, EpgList
from apps.epg_admin.tables import is_authenticated_request
from oldman.web.components.charts import (
    ChartResult,
    ChartSeries,
    ChartSummary,
    SQLAlchemyChartView,
    TailwindChartRenderer,
)
from oldman.web.components.charts.views import ChartInvalidRequest
from oldman.web.request import Request
from oldman.web.routing import get_app

app = get_app()


class DashboardProgrammeTrendChart(SQLAlchemyChartView):
    """首页节目数量趋势图表 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "dashboard_programme_trend_chart"
    route_path = "/dashboard/charts/programme-trend"
    chart_type = "line"
    default_range = "30d"
    default_metric = "programmes"
    allowed_ranges = ("7d", "30d", "90d")
    allowed_metrics = ("programmes",)
    allowed_chart_types = ("line",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取首页图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """按日期聚合节目数量，并返回 ApexCharts 配置。"""
        days = parse_range_days(chart_request.range_key)
        start_at = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days - 1)
        date_expr = func.date(EpgList.start_date)
        result = await self.require_db_session().execute(
            select(date_expr.label("day"), func.count(EpgList.id).label("total"))
            .where(EpgList.start_date >= start_at)
            .group_by(date_expr)
            .order_by(date_expr.asc())
        )
        rows = result.all()
        labels = [str(row.day) for row in rows]
        data = [int(row.total or 0) for row in rows]
        total = sum(data)
        return ChartResult(
            series=[ChartSeries(name="Programmes", data=data)] if data else [],
            labels=labels,
            summary=[ChartSummary(label="Total Programmes", value=total, tone="primary")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "line", "height": 320, "toolbar": {"show": False}},
        )


class DashboardFeedStatusChart(SQLAlchemyChartView):
    """首页 feed 状态分布图表 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "dashboard_feed_status_chart"
    route_path = "/dashboard/charts/feed-status"
    chart_type = "bar"
    default_range = "30d"
    default_metric = "feed_status"
    allowed_ranges = ("7d", "30d", "90d")
    allowed_metrics = ("feed_status",)
    allowed_chart_types = ("bar",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取首页图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """按 CatalogFeed.status 聚合 feed 数量。"""
        start_at = range_start_at(chart_request.range_key)
        status_expr = func.coalesce(CatalogFeed.status, "unknown")
        result = await self.require_db_session().execute(
            select(status_expr.label("status"), func.count(CatalogFeed.id).label("total"))
            .where(CatalogFeed.updated_at >= start_at)
            .group_by(status_expr)
            .order_by(func.count(CatalogFeed.id).desc(), status_expr.asc())
        )
        rows = result.all()
        data = [{"x": str(row.status or "unknown"), "y": int(row.total or 0)} for row in rows]
        total = sum(item["y"] for item in data)
        return ChartResult(
            series=[ChartSeries(name="Feeds", data=data)] if data else [],
            labels=[item["x"] for item in data],
            summary=[ChartSummary(label="Total Feeds", value=total, tone="success")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "bar", "height": 320, "toolbar": {"show": False}},
        )


class DashboardLogoQualityChart(SQLAlchemyChartView):
    """首页 logo 质量分布图表 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "dashboard_logo_quality_chart"
    route_path = "/dashboard/charts/logo-quality"
    chart_type = "bar"
    default_range = "30d"
    default_metric = "logo_quality"
    allowed_ranges = ("7d", "30d", "90d")
    allowed_metrics = ("logo_quality",)
    allowed_chart_types = ("bar",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取首页图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """按质量分桶聚合当前 logo 资源。"""
        start_at = range_start_at(chart_request.range_key)
        bucket_expr = case(
            (CatalogLogoAsset.quality_score < 40, "Low"),
            (CatalogLogoAsset.quality_score < 70, "Medium"),
            else_="High",
        )
        order_expr = case(
            (CatalogLogoAsset.quality_score < 40, 1),
            (CatalogLogoAsset.quality_score < 70, 2),
            else_=3,
        )
        result = await self.require_db_session().execute(
            select(bucket_expr.label("bucket"), func.count(CatalogLogoAsset.id).label("total"), func.min(order_expr).label("sort_order"))
            .where(CatalogLogoAsset.updated_at >= start_at)
            .group_by(bucket_expr)
            .order_by("sort_order")
        )
        rows = result.all()
        data = [{"x": str(row.bucket), "y": int(row.total or 0)} for row in rows]
        total = sum(item["y"] for item in data)
        return ChartResult(
            series=[ChartSeries(name="Logo Assets", data=data)] if data else [],
            labels=[item["x"] for item in data],
            summary=[ChartSummary(label="Total Logos", value=total, tone="warning")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "bar", "height": 320, "toolbar": {"show": False}},
        )


def parse_range_days(range_key: str) -> int:
    """把图表时间范围转换为天数，非法值返回请求错误。"""
    allowed = {"7d": 7, "30d": 30, "90d": 90}
    try:
        return allowed[range_key]
    except KeyError:
        raise ChartInvalidRequest(f"Unknown chart range: {range_key}") from None


def range_start_at(range_key: str) -> dt.datetime:
    """把 Dashboard range 转换为当前窗口起始时间。"""
    days = parse_range_days(range_key)
    return dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days - 1)


app.add_route(DashboardProgrammeTrendChart.as_view(), DashboardProgrammeTrendChart.route_path, name=DashboardProgrammeTrendChart.route_name)
app.add_route(DashboardFeedStatusChart.as_view(), DashboardFeedStatusChart.route_path, name=DashboardFeedStatusChart.route_name)
app.add_route(DashboardLogoQualityChart.as_view(), DashboardLogoQualityChart.route_path, name=DashboardLogoQualityChart.route_name)
