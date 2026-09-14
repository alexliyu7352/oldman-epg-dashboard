"""Oldman 后端 Chart 组件测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from typing import cast

from sanic import Sanic
from sanic.exceptions import SanicException

import oldman
from oldman.db import DatabaseManager
from oldman.web.api.enums import ApiErrorCode
from oldman.web.components.charts import (
    BaseChartView,
    ChartConfig,
    ChartField,
    ChartRequest,
    ChartResult,
    ChartSeries,
    ChartSummary,
    SQLAlchemyChartView,
)

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


def response_body(response: object) -> bytes:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise AssertionError("response has no byte body")
    return body


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


class StrictChartView(DemoChartView):
    """测试用严格参数白名单图表视图。"""

    allowed_ranges = ("7d", "30d")
    allowed_group_by = ("day",)
    allowed_metrics = ("programmes",)
    allowed_chart_types = ("line",)


class DeniedChartView(DemoChartView):
    """拒绝访问的测试图表。"""

    async def check_auth(self, request) -> bool:
        """拒绝当前请求访问图表。"""
        return False


class DeniedSQLAlchemyChartView(SQLAlchemyChartView):
    """拒绝访问的 SQLAlchemy 图表。"""

    async def check_auth(self, request) -> bool:
        """拒绝当前请求访问图表。"""
        return False


class FakeReadSession:
    """不访问真实数据库的异步 session 上下文。"""

    async def __aenter__(self):
        """进入假的只读 session。"""
        return object()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """离开假的只读 session。"""


class FakeDbManager:
    """提供 SQLAlchemyChartView 测试用只读 session。"""

    def get_read_session(self) -> FakeReadSession:
        """返回假的只读 session 上下文。"""
        return FakeReadSession()


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


class ChartRequestResultTest(unittest.TestCase):
    """验证 Chart 请求和结果协议。"""

    def test_chart_request_normalizes_sanic_multivalue_args(self) -> None:
        """Sanic 多值查询参数必须规范为首个值。"""
        request = make_chart_request(args={"range": ["30d"], "group_by": ["day"], "chart_type": ["line"], "metric": ["programmes"]})

        chart_request = DemoChartView().build_chart_request(request, route_kwargs={})

        self.assertEqual(chart_request.range_key, "30d")
        self.assertEqual(chart_request.group_by, "day")
        self.assertEqual(chart_request.chart_type, "line")
        self.assertEqual(chart_request.metric, "programmes")

    def test_chart_config_is_exported_as_component_contract(self) -> None:
        """Chart 配置对象必须作为后端组件协议导出。"""
        config = ChartConfig(
            chart_type="bar",
            default_range="30d",
            default_group_by="status",
            default_metric="feeds",
            fields=(ChartField(key="status", label="Status"),),
        )

        self.assertEqual(config.chart_type, "bar")
        self.assertEqual(config.fields[0].key, "status")

    def test_chart_result_converts_to_apex_payload(self) -> None:
        """ChartResult 必须转换成前端 ApexChart 可消费的 JSON 配置。"""
        result = ChartResult(
            series=[ChartSeries(name="Programmes", data=[1, 2, 3])],
            labels=["2026-06-08", "2026-06-09", "2026-06-10"],
            summary=[ChartSummary(label="Total", value=6)],
            meta={"range": "3d"},
            chart={"type": "line", "height": 320},
        )

        payload = result.to_apex_options()

        self.assertEqual(payload["series"], [{"name": "Programmes", "data": [1, 2, 3]}])
        self.assertEqual(payload["labels"], ["2026-06-08", "2026-06-09", "2026-06-10"])
        self.assertEqual(payload["chart"], {"type": "line", "height": 320})
        self.assertEqual(payload["meta"], {"range": "3d"})
        self.assertEqual(payload["summary"], [{"label": "Total", "value": 6, "tone": "secondary"}])


class ChartRendererTest(unittest.TestCase):
    """验证 Chart 模板 renderer。"""

    def test_bootstrap_chart_renderer_outputs_oldman_apex_shell(self) -> None:
        """Bootstrap Chart renderer 必须输出 Oldman ApexChart shell。"""
        chart = DemoChartView(request=make_chart_request())

        html = str(asyncio.run(chart.render_shell(html_id="programme-trend-chart")))

        self.assertIn('id="programme-trend-chart"', html)
        self.assertIn('data-om-component="apex-chart"', html)
        self.assertIn('data-om-chart-src="/dashboard/charts/programme-trend"', html)
        self.assertIn("data-om-chart-target", html)
        self.assertIn("data-om-chart-loading", html)
        self.assertNotIn("data-om-scoped-preloader", html)
        self.assertIn("data-om-chart-empty", html)
        self.assertIn("data-om-chart-error", html)

    def test_tailwind_chart_shell_keeps_protocol_in_template(self) -> None:
        """Tailwind Chart shell 必须在模板中声明协议节点。"""
        package_root = Path(oldman.__file__ or "").resolve().parent
        template = (package_root / "web/templates/oldman/charts/default/shell.html").read_text()

        self.assertIn("data-om-chart-target", template)
        self.assertIn("data-om-chart-loading", template)
        self.assertNotIn("data-om-scoped-preloader", template)
        self.assertIn("data-om-chart-empty", template)
        self.assertIn("data-om-chart-error", template)
        self.assertNotIn("data-om-chart-config", template)


class ChartViewLifecycleTest(unittest.TestCase):
    """验证 ChartView HTTP 生命周期。"""

    def test_chart_view_returns_json_payload(self) -> None:
        """ChartView GET 必须返回 ApexCharts JSON 配置。"""
        response = asyncio.run(DemoChartView().get(make_chart_request(headers={"accept": "application/json"})))

        self.assertEqual(response.status, 200)
        body = response_body(response)
        self.assertIn(b'"series"', body)
        self.assertIn(b'"Programmes"', body)

    def test_chart_view_rejects_unknown_filter(self) -> None:
        """未知 filter 参数必须返回 400。"""
        response = asyncio.run(DemoChartView().get(make_chart_request(args={"filter.unknown": ["1"]})))

        self.assertEqual(response.status, 400)
        self.assertIn(b"Unknown chart filter", response_body(response))

    def test_chart_view_rejects_values_outside_declared_whitelists(self) -> None:
        """图表声明参数白名单后，非法 range、group_by、metric 和 chart_type 必须返回 400。"""
        cases = (
            {"range": ["365d"]},
            {"group_by": ["month"]},
            {"metric": ["users"]},
            {"chart_type": ["pie"]},
        )

        for args in cases:
            with self.subTest(args=args):
                response = asyncio.run(StrictChartView().get(make_chart_request(args=args)))

                self.assertEqual(response.status, 400)
                self.assertIn(b"Invalid chart parameter", response_body(response))

    def test_chart_view_rejects_non_default_parameter_when_no_whitelist_is_declared(self) -> None:
        """图表未声明白名单时，只允许默认参数值，不能放行任意 group_by。"""
        response = asyncio.run(DemoChartView().get(make_chart_request(args={"group_by": ["month"]})))

        self.assertEqual(response.status, 400)
        self.assertIn(b"Invalid chart parameter: group_by", response_body(response))

    def test_chart_view_permission_denied_returns_403(self) -> None:
        """权限检查失败必须返回 403。"""
        response = asyncio.run(DeniedChartView().get(make_chart_request()))

        self.assertEqual(response.status, 403)
        self.assertIn(b"Permission denied", response_body(response))

    def test_sqlalchemy_chart_view_is_exported(self) -> None:
        """SQLAlchemyChartView 必须作为正式 adapter 导出。"""
        self.assertTrue(issubclass(SQLAlchemyChartView, BaseChartView))

    def test_sqlalchemy_chart_view_permission_denied_uses_permission_code(self) -> None:
        """SQLAlchemyChartView 权限失败必须返回统一权限错误码。"""
        manager = FakeDbManager()

        class CustomDatabaseChart(DeniedSQLAlchemyChartView):
            """显式绑定测试数据库 manager。"""

            database_manager = cast(DatabaseManager, manager)

        response = asyncio.run(CustomDatabaseChart().get(make_chart_request()))

        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(response_body(response))["error_code"], ApiErrorCode.PERMISSION_DENIED)


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
        self.assertIn('allowed_ranges = ("7d", "30d", "90d")', source)
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
