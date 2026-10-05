"""EPG 业务图表测试；框架图表本身的行为由框架仓库的测试覆盖。"""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path

from sanic import Sanic
from sanic.exceptions import SanicException

from oldman.web.components.charts import (
    BaseChartView,
    ChartRequest,
    ChartResult,
    ChartSeries,
)

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


class DemoChartView(BaseChartView):
    """测试用图表视图。"""

    route_name = "dashboard_programme_trend_chart"
    route_path = "/dashboard/charts/programme-trend"
    chart_type = "line"
    default_range = "7d"

    async def get_result(self, chart_request):
        """返回测试图表结果。"""
        return ChartResult(
            series=[ChartSeries(name="Programmes", data=[1, 2])],
            labels=["2026-06-09", "2026-06-10"],
            meta={"range": chart_request.range_key},
            chart={"type": chart_request.chart_type},
        )


class CapturingChartSession:
    """记录 Dashboard 图表提交给 SQLAlchemy 的查询。"""

    def __init__(self) -> None:
        """初始化捕获状态。"""
        self.statement = None

    async def execute(self, statement):
        """记录查询并返回空结果。"""
        self.statement = statement
        return EmptyChartRows()


class EmptyChartRows:
    """Dashboard 图表 SQL 测试用空结果。"""

    def all(self) -> list[object]:
        """返回空行集合。"""
        return []


class DashboardChartIntegrationContractTest(unittest.TestCase):
    """验证 Dashboard 图表接入边界。"""

    def test_dashboard_programme_trend_chart_uses_real_chart_view(self) -> None:
        """Dashboard 必须使用独立 ChartView endpoint。"""
        source = (EXAMPLE_ROOT / "apps/dashboard/chart_views.py").read_text()
        page_source = (EXAMPLE_ROOT / "apps/dashboard/views.py").read_text()

        self.assertIn("class DashboardProgrammeTrendChart(SQLAlchemyChartView)", source)
        self.assertIn("class DashboardFeedStatusChart(SQLAlchemyChartView)", source)
        self.assertIn("class DashboardLogoQualityChart(SQLAlchemyChartView)", source)
        self.assertIn('route_path = "/dashboard/charts/programme-trend"', source)
        self.assertIn('route_path = "/dashboard/charts/feed-status"', source)
        self.assertIn('route_path = "/dashboard/charts/logo-quality"', source)
        self.assertIn("DashboardProgrammeTrendChart.as_view()", source)
        self.assertIn("DashboardFeedStatusChart.as_view()", source)
        self.assertIn("DashboardLogoQualityChart.as_view()", source)
        self.assertIn("async def get_result", source)
        self.assertIn('allowed_ranges = ("all", "7d", "30d", "90d")', source)
        self.assertIn('allowed_metrics = ("programmes",)', source)
        self.assertIn('allowed_metrics = ("feed_status",)', source)
        self.assertIn('allowed_metrics = ("logo_quality",)', source)
        self.assertNotIn("class DashboardProgrammeTrendChart", page_source)
        self.assertNotIn("select(", page_source)

    def test_dashboard_does_not_inline_one_off_apex_json(self) -> None:
        """Dashboard 不允许内联一次性 ApexCharts JSON/options。"""
        views_source = (EXAMPLE_ROOT / "apps/dashboard/views.py").read_text()
        chart_views_source = (EXAMPLE_ROOT / "apps/dashboard/chart_views.py").read_text()
        template_source = (EXAMPLE_ROOT / "templates/pages/dashboard.html").read_text()
        analytics_template_source = (EXAMPLE_ROOT / "templates/pages/dashboard_analytics.html").read_text()

        self.assertNotIn("ApexCharts(", views_source + chart_views_source + template_source + analytics_template_source)
        self.assertNotIn("data-om-chart-config", template_source)
        self.assertNotIn("data-om-chart-config", analytics_template_source)
        self.assertIn("programme_trend_chart.render_shell", template_source)
        self.assertIn("feed_status_chart.render_shell", template_source)
        self.assertIn("logo_quality_chart.render_shell", template_source)
        self.assertIn("programme_trend_chart.render_shell", analytics_template_source)
        self.assertIn("feed_status_chart.render_shell", analytics_template_source)
        self.assertIn("logo_quality_chart.render_shell", analytics_template_source)
        shell = str(asyncio.run(DemoChartView(request=make_chart_request()).render_shell()))
        self.assertIn('data-om-chart-src="/dashboard/charts/programme-trend"', shell)

    def test_dashboard_views_do_not_register_mock_user_edit_routes(self) -> None:
        """Dashboard 不允许注册旧 demo 用户编辑弹窗路由。"""
        source = (EXAMPLE_ROOT / "apps/dashboard/views.py").read_text()

        self.assertNotIn('"/users/edit/modal"', source)
        self.assertNotIn('"/users/edit"', source)
        self.assertNotIn("users_edit_modal", source)

    def test_dashboard_range_charts_filter_real_business_timestamps(self) -> None:
        """Dashboard range 切换必须进入真实 SQL 查询，不能只回显 meta。"""
        chart_views = load_dashboard_chart_views_module()

        for chart_class, table_name in (
            (chart_views.DashboardFeedStatusChart, "catalog_feed"),
            (chart_views.DashboardLogoQualityChart, "catalog_logo_asset"),
        ):
            with self.subTest(chart=chart_class.__name__):
                session = CapturingChartSession()
                chart = chart_class()
                chart.db_session = session

                asyncio.run(chart.get_result(dashboard_chart_request(range_key="7d", metric=chart.default_metric, chart_type=chart.chart_type)))

                statement = str(session.statement)
                self.assertIn(f"WHERE {table_name}.updated_at >=", statement)

    def test_dashboard_charts_default_to_every_record(self) -> None:
        """演示数据的时间是固定的，过一段时间就落在任何"最近 N 天"之外；默认的"全部"不按时间过滤。"""
        chart_views = load_dashboard_chart_views_module()

        for chart_class in (
            chart_views.DashboardProgrammeTrendChart,
            chart_views.DashboardFeedStatusChart,
            chart_views.DashboardLogoQualityChart,
        ):
            with self.subTest(chart=chart_class.__name__):
                session = CapturingChartSession()
                chart = chart_class()
                chart.db_session = session

                self.assertEqual("all", chart.default_range)
                asyncio.run(chart.get_result(dashboard_chart_request(range_key="all", metric=chart.default_metric, chart_type=chart.chart_type)))

                self.assertNotIn("WHERE", str(session.statement))

    def test_epg_admin_models_include_dashboard_catalog_tables(self) -> None:
        """Dashboard Overview 需要的 Catalog 表必须在当前后台模型层映射。"""
        source = (EXAMPLE_ROOT / "apps/epg_admin/models.py").read_text()

        for class_name in (
            "ChannelName",
            "UpstreamSourceRecord",
            "CatalogChannel",
            "CatalogFeed",
            "CatalogLogoAsset",
            "CatalogMatchDecision",
        ):
            self.assertIn(f"class {class_name}", source)

    def test_logo_asset_workbench_uses_chart_views(self) -> None:
        """Logo 质量工作台必须通过独立 ChartView endpoint 提供真实分布图。"""
        views_source = (EXAMPLE_ROOT / "apps/epg_admin/views.py").read_text()
        template_source = (EXAMPLE_ROOT / "templates/pages/logo_assets/index.html").read_text()

        self.assertIn("LogoAssetQualityDistributionChart.as_view()", views_source)
        self.assertIn("LogoAssetMimeDistributionChart.as_view()", views_source)
        self.assertIn("LogoAssetDimensionScatterChart.as_view()", views_source)
        self.assertIn('route_path = "/logo-assets/charts/quality-distribution"', views_source)
        self.assertIn('route_path = "/logo-assets/charts/mime-distribution"', views_source)
        self.assertIn('route_path = "/logo-assets/charts/dimensions"', views_source)
        self.assertNotIn("ApexCharts(", views_source + template_source)
        self.assertNotIn("data-om-chart-config", template_source)
        self.assertIn("quality_distribution_chart.render_shell", template_source)
        self.assertIn("mime_distribution_chart.render_shell", template_source)
        self.assertIn("dimension_scatter_chart.render_shell", template_source)


def make_chart_request(*, args: dict[str, list[str]] | None = None, headers: dict[str, str] | None = None):
    """构造接近 Sanic request 的 Chart 测试请求。"""

    class Args(dict):
        """模拟 Sanic request.args 的多值查询参数。"""

        def get(self, key, default=None):
            """读取单个查询参数。"""
            return super().get(key, default)

    class Headers(dict):
        """模拟 Sanic request.headers 的大小写无关读取。"""

        def get(self, key, default=None):
            """读取请求头。"""
            return super().get(key.lower(), default)

    return type(
        "ChartRequestStub",
        (),
        {
            "args": Args(args or {}),
            "headers": Headers({(key or "").lower(): value for key, value in (headers or {}).items()}),
            "app": None,
        },
    )()


def dashboard_chart_request(*, range_key: str, metric: str, chart_type: str) -> ChartRequest:
    """构造 Dashboard 图表 get_result 单测用请求。"""
    return ChartRequest(
        request=make_chart_request(),
        range_key=range_key,
        group_by="",
        chart_type=chart_type,
        metric=metric,
        filters={},
        route_kwargs={},
    )


def load_dashboard_chart_views_module():
    """在临时 Sanic app 中隔离加载 dashboard chart module。"""
    app_name = "dashboard_chart_contract_test"
    try:
        Sanic.unregister_app(Sanic.get_app(app_name))
    except SanicException:
        pass

    Sanic(app_name)
    spec = importlib.util.spec_from_file_location("dashboard_chart_views_contract_test", (EXAMPLE_ROOT / "apps/dashboard/chart_views.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load dashboard chart views module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            Sanic.unregister_app(Sanic.get_app(app_name))
        except SanicException:
            pass
    return module
