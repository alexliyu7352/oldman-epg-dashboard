"""Oldman 后端 Table 新协议测试。"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from apps.epg_admin.models import CatalogFeed, CatalogLogoAsset, CatalogMatchDecision, ChannelName, ChannelsEpg, EpgList, UpstreamSourceRecord
from markupsafe import Markup
from sanic import Sanic
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from oldman.db import DatabaseManager
from oldman.web.api.enums import ApiErrorCode
from oldman.web.components.tables import BaseTableView, Column, SQLAlchemyTableView, TableRenderer, TableResult
from oldman.web.components.tables.views import TableInvalidRequest, TableValidationError


def response_body(response: object) -> bytes:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise AssertionError("response has no byte body")
    return body


def column_declarations(table_class: type[BaseTableView]) -> list[object]:
    """把延迟翻译标签归一化后比较列的公开声明。"""
    return [
        (str(column[0]), *column[1:]) if isinstance(column, tuple) else column
        for column in table_class.columns
    ]


class TableColumnContractTest(unittest.TestCase):
    """验证 Table 列声明协议。"""

    def test_column_normalization_matches_document_contract(self) -> None:
        """列解析应该支持旧式声明、显式 Column 和虚拟列默认能力。"""

        class DemoTable(BaseTableView):
            """测试用结构化数据表格。"""

            route_name = "demo_table"
            route_path = "/demo/table"
            columns = [
                "id",
                ("Title", "title", "get_column_title_data"),
                ("Action", None, "get_column_action_data"),
                Column("created_at", label="Created"),
            ]
            search_fields = ["title"]

        columns = DemoTable().get_columns()

        self.assertEqual([column.name for column in columns], ["id", "title", "action", "created_at"])
        self.assertEqual(columns[0].field_path, "id")
        self.assertTrue(columns[0].sortable)
        self.assertFalse(columns[0].searchable)
        self.assertTrue(columns[1].searchable)
        self.assertFalse(columns[2].sortable)
        self.assertFalse(columns[2].searchable)
        self.assertEqual(columns[2].field_path, None)
        self.assertFalse(hasattr(columns[0], "sort_expression"))

    def test_column_sortable_is_internal_metadata(self) -> None:
        """业务代码不能通过 Column(sortable=...) 绕过表格类排序配置。"""
        with self.assertRaises(TypeError):
            Column("title", sortable=False)  # pyright: ignore[reportCallIssue] -- intentionally invalid public call


@dataclass
class Channel:
    """测试用频道对象。"""

    name: str


@dataclass
class Programme:
    """测试用节目对象。"""

    id: int
    title: str
    channel: Channel
    hits: int


class DemoProgramTable(BaseTableView):
    """结构化数据表格测试类。"""

    route_name = "program_table"
    route_path = "/programmes/table"
    columns = [
        "id",
        ("Title", "title", "get_column_title_data"),
        ("Channel", "channel.name"),
        ("Hits", "hits"),
    ]
    search_fields = ["title", "channel.name"]
    ordering = ["title"]
    page_size = 2
    page_size_options = [1, 2, 5]
    max_page_size = 5

    async def get_object_list(self):
        """返回结构化测试数据。"""
        return [
            Programme(id=1, title="Zoo News", channel=Channel("BBC"), hits=3),
            Programme(id=2, title="Alpha Show", channel=Channel("CNN"), hits=8),
            Programme(id=3, title="Beta News", channel=Channel("BBC"), hits=5),
        ]

    async def filter_channel(self, rows, value, table_request):
        """按频道名称筛选测试数据。"""
        return [row for row in rows if row.channel.name == value]

    def get_column_title_data(self, row, **kwargs):
        """返回复杂显示值和本地排序原始值。"""
        return f"<a href=\"/programmes/{row.id}\">{row.title}</a>", row.title


class DictProgramTable(BaseTableView):
    """字典列表结构化数据表格测试类。"""

    route_name = "dict_program_table"
    route_path = "/dict-programmes/table"
    columns = [
        "id",
        ("Title", "title"),
        ("Channel", "channel.name"),
        ("Hits", "hits"),
    ]
    search_fields = ["title", "channel.name"]
    ordering = ["title"]
    page_size = 2
    page_size_options = [1, 2, 5]
    max_page_size = 5

    async def get_object_list(self):
        """返回字典结构测试数据。"""
        return [
            {"id": 1, "title": "Zoo News", "channel": {"name": "BBC"}, "hits": 3},
            {"id": 2, "title": "Alpha Show", "channel": {"name": "CNN"}, "hits": 8},
            {"id": 3, "title": "Beta News", "channel": {"name": "BBC"}, "hits": 5},
        ]

    async def filter_channel(self, rows, value, table_request):
        """按嵌套字典中的频道名称筛选测试数据。"""
        return [row for row in rows if row["channel"]["name"] == value]


class ComplexRawValueTable(BaseTableView):
    """显式返回复杂 raw value 的测试表格。"""

    route_name = "complex_raw_table"
    route_path = "/complex-raw/table"
    columns = [("Meta", "title", "get_column_meta_data")]

    async def get_object_list(self):
        """返回用于验证 JSON raw_values 边界的数据。"""
        return [
            {"id": 1, "title": "Alpha Show"},
        ]

    def get_column_meta_data(self, row, **kwargs):
        """返回复杂 raw value，renderer 必须把它收口为 JSON 标量。"""
        return "<strong>Alpha Show</strong>", {"sort": 1}


class SelectableProgramTable(DemoProgramTable):
    """启用批量选择列的结构化测试表格。"""

    selectable = True


class DeniedProgramTable(DemoProgramTable):
    """拒绝访问的结构化测试表格。"""

    async def check_auth(self, request) -> bool:
        """拒绝当前请求访问表格数据。"""
        return False


class ScopedDictProgramTable(BaseTableView):
    """带服务端固定范围的字典数据表格。"""

    route_name = "scoped_dict_program_table"
    route_path = "/scoped-dict-programmes/table"
    columns = ["id", "title", "tenant_id"]

    async def get_object_list(self):
        """返回包含多个租户的数据，用于验证固定限制不会暴露给前端。"""
        return [
            {"id": 1, "title": "Owned News", "tenant_id": "owned"},
            {"id": 2, "title": "Other News", "tenant_id": "other"},
        ]

    async def apply_base_filters(self, rows: list[dict[str, object]], table_request):
        """固定限制当前用户只能看到 owned 租户。"""
        return [row for row in rows if row["tenant_id"] == "owned"]

    async def filter_tenant_id(self, rows: list[dict[str, object]], value: object, table_request):
        """模拟同名可见筛选，不能突破 apply_base_filters 结果集。"""
        return [row for row in rows if row["tenant_id"] == value]


class TableStructuredDataTest(unittest.TestCase):
    """验证结构化数据源和响应协议。"""

    def test_structured_data_query_supports_search_filter_sort_and_page_size(self) -> None:
        """结构化数据源应该按字段路径完成搜索、筛选、排序和分页。"""
        table = DemoProgramTable()
        request = table.build_table_request(
            make_request(args={"q": "news", "filter.channel": "BBC", "sort": "-hits", "page_size": "1", "page": "1"}),
            route_kwargs={},
        )

        result = asyncio.run(table.query_result(request))

        self.assertEqual(result.total, 3)
        self.assertEqual(result.filtered_total, 2)
        self.assertEqual(result.page_size, 1)
        self.assertEqual([row.id for row in result.rows], [3])

    def test_dict_data_query_supports_search_filter_sort_and_page_size(self) -> None:
        """字典列表数据源应该复用同一套搜索、筛选、排序和分页生命周期。"""
        table = DictProgramTable()
        request = table.build_table_request(
            make_request(args={"q": "news", "filter.channel": "BBC", "sort": "-hits", "page_size": "1", "page": "1"}),
            route_kwargs={},
        )

        result = asyncio.run(table.query_result(request))

        self.assertEqual(result.total, 3)
        self.assertEqual(result.filtered_total, 2)
        self.assertEqual(result.page_size, 1)
        self.assertEqual([row["id"] for row in result.rows], [3])

    def test_table_request_normalizes_sanic_multivalue_filter_args(self) -> None:
        """Sanic 查询参数 items 可能返回 list，Table filter 必须取首个值而不是 500。"""
        table = DemoProgramTable()

        request = table.build_table_request(
            make_sanic_args_request(args={"filter.channel": ["BBC"], "page_size": ["1"]}),
            route_kwargs={},
        )

        self.assertEqual(request.filters, {"channel": "BBC"})
        self.assertEqual(request.page_size, 1)

    def test_frontend_filter_cannot_override_server_side_base_filter(self) -> None:
        """普通 filter 参数不能扩大服务端固定限制后的结果范围。"""
        table = ScopedDictProgramTable()
        request = table.build_table_request(
            make_request(args={"filter.tenant_id": "other"}),
            route_kwargs={},
        )

        result = asyncio.run(table.query_result(request))

        self.assertEqual(result.total, 2)
        self.assertEqual(result.filtered_total, 0)
        self.assertEqual(result.rows, [])

    def test_table_request_rejects_invalid_page_and_page_size(self) -> None:
        """非法分页参数应该返回请求错误，不能静默修正。"""
        table = DemoProgramTable()

        for args in (
            {"page": "abc"},
            {"page": "0"},
            {"page_size": "abc"},
            {"page_size": "0"},
            {"page_size": "6"},
        ):
            with self.subTest(args=args):
                with self.assertRaises(TableInvalidRequest):
                    table.build_table_request(make_request(args=args), route_kwargs={})

    def test_render_shell_is_async_and_uses_route_name(self) -> None:
        """render_shell 应该异步生成组件根节点和 data endpoint 地址。"""
        app = Sanic("table_shell_test")
        try:
            app.add_route(DemoProgramTable.as_view(), DemoProgramTable.route_path, name=DemoProgramTable.route_name)
            table = DemoProgramTable(request=make_request(app=app))

            html = str(asyncio.run(table.render_shell()))

            self.assertIn('data-om-component="table"', html)
            self.assertIn('data-om-table-src="/programmes/table"', html)
            self.assertIn('data-om-table-page-size="2"', html)
            self.assertIn('data-om-table-partial', html)
        finally:
            Sanic.unregister_app(app)

    def test_table_does_not_expose_sync_render_api(self) -> None:
        """Table 正式渲染 API 必须保持异步。"""
        self.assertFalse(hasattr(BaseTableView, "render_html_fragment_sync"))

    def test_component_entrypoint_exposes_table_api(self) -> None:
        """Table API 只从职责明确的组件包公开。"""
        import oldman.web as ui
        from oldman.web.components import tables

        self.assertIs(tables.BaseTableView, BaseTableView)
        self.assertIs(tables.SQLAlchemyTableView, SQLAlchemyTableView)
        self.assertIs(tables.Column, Column)
        self.assertIs(tables.TableResult, TableResult)
        self.assertFalse(hasattr(ui, "BaseTableView"))

    def test_render_shell_outputs_initial_filter_data_attributes(self) -> None:
        """render_shell 应该把初始可见筛选输出为 data-om-filter-* 属性。"""
        table = DemoProgramTable(initial_filters={"channel_id": 12, "date_from": "2026-06-01"})

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-filter-channel-id="12"', html)
        self.assertIn('data-om-filter-date-from="2026-06-01"', html)

    def test_render_shell_outputs_initial_sort_data_attribute(self) -> None:
        """render_shell 应该把初始排序输出为前端可继续提交的可见状态。"""
        table = DemoProgramTable(initial_sort="-hits")

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-table-initial-sort="-hits"', html)

    def test_page_size_options_preserve_integer_protocol(self) -> None:
        """迁移不能把原先 int() 接受的分页配置对象缩窄为字符串解析。"""

        class IntegerLike:
            def __int__(self) -> int:
                return 7

        table = DemoProgramTable()
        table.page_size_options = cast(Any, [IntegerLike()])
        table.max_page_size = 10

        self.assertIn("7", TableRenderer(table).resolve_page_size_options())

    def test_render_shell_restores_sort_and_page_size_from_list_url(self) -> None:
        """从编辑页历史后退时，服务端 shell 必须恢复列表 URL 中的表格状态。"""
        table = DemoProgramTable(request=make_request(args={"sort": "hits", "page_size": "5"}))

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-table-initial-sort="hits"', html)
        self.assertIn('data-om-table-page-size="5"', html)
        self.assertIn('data-om-table-page-size-options="1,2,5"', html)

    def test_render_shell_outputs_initial_query_in_filter_input(self) -> None:
        """render_shell 应该把初始查询输出到搜索框，供首次请求继续提交。"""
        table = DemoProgramTable(initial_query="BBC News")

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-table-filter', html)
        self.assertIn('value="BBC News"', html)

    def test_render_shell_outputs_table_controls_for_server_mode(self) -> None:
        """render_shell 应该输出搜索、加载和错误状态 hook，分页大小交给 fragment 底部。"""
        table = DemoProgramTable()

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-table-filter', html)
        self.assertNotIn('data-om-table-page-size-control', html)
        self.assertIn('data-om-table-loading', html)
        self.assertIn('data-om-table-error', html)

    def test_render_shell_outputs_initial_table_structure_for_remote_loading(self) -> None:
        """远程表格首屏 shell 应该先输出稳定表头和内部 loading 状态。"""
        table = DemoProgramTable()

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-table-partial', html)
        self.assertIn('data-om-initial-table-partial', html)
        self.assertIn('class="om-table-shell"', html)
        self.assertIn("<thead>", html)
        self.assertIn('data-om-table-body', html)
        self.assertIn('data-om-table-initial-loading', html)
        self.assertIn("Loading...", html)

    def test_table_rendering_delegates_to_renderer_class(self) -> None:
        """Table 输出应该委托 renderer_class，便于主题子类替换模板。"""

        class MinimalRenderer(TableRenderer):
            """测试用 renderer，模拟完全不同的模板输出。"""

            async def render_shell(
                self,
                *,
                route_kwargs,
                html_id=None,
                show_search=True,
                data_format="html",
            ):
                """返回自定义 shell，证明核心 Table 不直接拼主题结构。"""
                del route_kwargs, html_id, show_search, data_format
                return Markup('<section data-custom-table="1"></section>')

        class CustomRenderedTable(DemoProgramTable):
            """使用自定义 renderer 的测试表格。"""

            renderer_class = MinimalRenderer

        html = str(asyncio.run(CustomRenderedTable().render_shell()))

        self.assertEqual(html, '<section data-custom-table="1"></section>')

    def test_html_fragment_outputs_rows_summary_and_pagination_hooks(self) -> None:
        """HTML 片段应该输出行、总数摘要和服务端分页按钮。"""
        table = DemoProgramTable()
        request = table.build_table_request(make_request(args={"page_size": "1", "page": "1"}), route_kwargs={})
        result = TableResult(
            rows=[Programme(id=1, title="Zoo News", channel=Channel("BBC"), hits=3)],
            row_contexts=[{}],
            total=3,
            filtered_total=3,
            page=1,
            page_size=1,
        )

        html = str(asyncio.run(table.render_html_fragment(request, result)))

        self.assertIn("data-om-table-row", html)
        self.assertIn("data-om-table-body", html)
        self.assertIn('data-om-column-label="Title"', html)
        self.assertIn('data-om-column-label="Channel"', html)
        self.assertIn('data-om-table-page-size-control', html)
        self.assertIn('<option value="1" selected>1</option>', html)
        self.assertIn('<option value="2">2</option>', html)
        self.assertIn("data-om-table-summary", html)
        self.assertIn("Showing 1", html)
        self.assertIn("of 3 entries", html)
        self.assertIn("data-om-table-pagination", html)
        self.assertIn('data-om-table-page="2"', html)
        self.assertLess(html.index('data-om-table-page-size-control'), html.index("data-om-table-summary"))

    def test_html_fragment_falls_back_to_class_page_size_options(self) -> None:
        """请求实例意外覆盖 page_size_options 时，片段仍应保留类默认分页选项。"""
        table = DemoProgramTable()
        table.page_size_options = []
        request = table.build_table_request(make_request(args={"page_size": "1", "page": "1"}), route_kwargs={})
        result = TableResult(rows=[], row_contexts=[], total=3, filtered_total=3, page=1, page_size=1)

        html = str(asyncio.run(table.render_html_fragment(request, result)))

        self.assertIn('<option value="1" selected>1</option>', html)
        self.assertIn('<option value="2">2</option>', html)
        self.assertIn('<option value="5">5</option>', html)

    def test_html_head_outputs_visible_sort_controls(self) -> None:
        """表头应该输出 Tailwind 表格排序按钮和列 metadata。"""
        table = DemoProgramTable()

        html = str(table.render_html_head())

        self.assertIn('class="sort sorting"', html)
        self.assertIn('data-om-table-sort="title"', html)
        self.assertIn('data-om-table-sort-icon', html)
        self.assertIn("ri-arrow-up-down-line", html)
        self.assertIn('data-om-column-type="string"', html)

    def test_html_pagination_is_windowed_for_large_result_sets(self) -> None:
        """大结果集分页不能一次性输出所有页码导致页面横向溢出。"""
        table = DemoProgramTable()
        result = TableResult(rows=[], row_contexts=[], total=700, filtered_total=700, page=350, page_size=1)

        html = table.render_html_pagination(result)

        self.assertIn('data-om-table-page="1"', html)
        self.assertIn('data-om-table-page="350"', html)
        self.assertIn('data-om-table-page="700"', html)
        self.assertLess(html.count("data-om-table-page="), 20)

    def test_json_response_contains_columns_cells_and_raw_values(self) -> None:
        """JSON 协议应该输出 columns、cells 和 raw_values。"""
        table = DemoProgramTable()
        request = table.build_table_request(make_request(headers={"accept": "application/json"}), route_kwargs={})
        result = asyncio.run(table.query_result(request))

        payload = table.render_json_payload(request, result)

        self.assertEqual([column["name"] for column in payload["columns"]], ["id", "title", "channel.name", "hits"])
        self.assertEqual(payload["rows"][0]["cells"]["title"], "&lt;a href=&#34;/programmes/2&#34;&gt;Alpha Show&lt;/a&gt;")
        self.assertEqual(payload["rows"][0]["raw_values"]["title"], "Alpha Show")
        self.assertIn("pagination", payload)

    def test_json_raw_values_are_always_scalars(self) -> None:
        """JSON raw_values 不能把 dict/list 复杂结构透传给前端。"""
        table = ComplexRawValueTable()
        request = table.build_table_request(make_request(headers={"accept": "application/json"}), route_kwargs={})
        result = asyncio.run(table.query_result(request))

        payload = table.render_json_payload(request, result)

        raw_value = payload["rows"][0]["raw_values"]["title"]
        self.assertIsInstance(raw_value, str)
        self.assertNotIsInstance(raw_value, dict)

    def test_selectable_table_outputs_selection_hooks_without_polluting_columns(self) -> None:
        """批量选择列应由 renderer 输出，不进入业务 columns 或 JSON cells。"""
        table = SelectableProgramTable()
        request = table.build_table_request(make_request(), route_kwargs={})
        result = asyncio.run(table.query_result(request))

        html = str(asyncio.run(table.render_html_fragment(request, result)))
        payload = table.render_json_payload(request, result)

        self.assertIn("data-om-table-select-all", html)
        self.assertIn("data-om-table-select-row", html)
        self.assertIn('data-om-column-label="Select"', html)
        self.assertEqual([column["name"] for column in payload["columns"]], ["id", "title", "channel.name", "hits"])
        self.assertNotIn("selection", payload["rows"][0]["cells"])

    def test_response_type_uses_accept_header_and_defaults_to_html(self) -> None:
        """响应类型应该由 Accept 请求头决定，未知请求默认 HTML。"""
        table = DemoProgramTable()

        self.assertEqual(table.resolve_response_type(make_request(headers={"accept": "application/json"})), "json")
        self.assertEqual(table.resolve_response_type(make_request(headers={"accept": "text/html"})), "html")
        self.assertEqual(table.resolve_response_type(make_request(headers={"accept": "*/*"})), "html")
        self.assertEqual(table.resolve_response_type(make_request(headers={})), "html")

    def test_permission_denied_uses_json_error_when_accept_requests_json(self) -> None:
        """权限拒绝也应该遵守 Table 请求头协商，JSON 请求返回统一错误 schema。"""
        response = asyncio.run(DeniedProgramTable().get(make_request(headers={"accept": "application/json"})))
        payload = json.loads(response_body(response).decode("utf-8"))

        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error_code"], ApiErrorCode.PERMISSION_DENIED)
        self.assertNotIn("errors", payload)
        self.assertEqual(payload["data"]["errors"]["table"], "Permission denied")

    def test_table_check_auth_allows_access_by_default(self) -> None:
        """Table check_auth 默认允许访问，业务表格按需重写。"""
        table = DemoProgramTable()

        self.assertTrue(asyncio.run(table.check_auth(make_request())))

    def test_unknown_filter_returns_table_error_fragment(self) -> None:
        """未知筛选字段应该返回当前表格错误片段和 400。"""
        table = DemoProgramTable()

        response = asyncio.run(table.get(make_request(args={"filter.unknown": "1"})))

        self.assertEqual(response.status, 400)
        body = response_body(response)
        self.assertIn(b"data-om-table-error", body)
        self.assertIn(b"Unknown table filter", body)

    def test_invalid_pagination_returns_table_error_fragment(self) -> None:
        """非法分页参数应该由 data endpoint 转换为当前表格错误片段。"""
        table = DemoProgramTable()

        response = asyncio.run(table.get(make_request(args={"page_size": "999"})))

        self.assertEqual(response.status, 400)
        body = response_body(response)
        self.assertIn(b"data-om-table-error", body)
        self.assertIn(b"Invalid table pagination parameter", body)

    def test_table_result_requires_row_contexts_to_match_rows(self) -> None:
        """row_contexts 必须和 rows 严格等长。"""
        with self.assertRaises(ValueError):
            TableResult(rows=[object()], row_contexts=[])

    def test_column_callback_receives_full_context_and_index_fallback(self) -> None:
        """列回调应该拿到完整上下文，并支持 get_column_<index>_data。"""

        class IndexFallbackTable(BaseTableView):
            """测试列索引回调的表格。"""

            route_name = "index_table"
            route_path = "/index/table"
            columns = [
                ("Virtual", None),
            ]

            def get_column_0_data(self, row, **kwargs):
                """记录列回调上下文并返回显示值。"""
                self.last_kwargs = kwargs
                return "virtual", "raw-virtual"

        table = IndexFallbackTable(request=make_request())
        result = TableResult(rows=[{"id": 1}], row_contexts=[{"shared": "ok"}], total=1, filtered_total=1)
        request = table.build_table_request(make_request(), route_kwargs={})

        html = str(asyncio.run(table.render_html_fragment(request, result)))

        self.assertIn("virtual", html)
        self.assertEqual(table.last_kwargs["field_path"], None)
        self.assertEqual(table.last_kwargs["row_context"], {"shared": "ok"})
        self.assertEqual(table.last_kwargs["row_index"], 0)
        self.assertEqual(table.last_kwargs["column_index"], 0)
        self.assertIs(table.last_kwargs["table"], table)
        self.assertIs(table.last_kwargs["request"], request.request)


class RelationSqlTable(SQLAlchemyTableView):
    """测试 SQLAlchemy 关系字段搜索和排序的表格。"""

    route_name = "relation_sql_table"
    route_path = "/relation/table"
    model = EpgList
    columns = [("Channel", "channel.name")]
    search_fields = ["channel.name"]
    ordering = ["channel.name"]

    async def get_queryset(self):
        """返回测试查询。"""
        return select(EpgList)


class TableSqlAlchemyRelationTest(unittest.TestCase):
    """验证 SQLAlchemy 关系字段查询不会退化为隐式笛卡尔积。"""

    def test_relation_search_adds_explicit_join(self) -> None:
        """关系字段搜索必须为关联表生成显式 JOIN。"""
        table = RelationSqlTable()
        request = table.build_table_request(make_request(args={"q": "BBC"}), route_kwargs={})

        query = asyncio.run(table.apply_search(select(EpgList), request))
        sql = str(query)

        self.assertIn("JOIN", sql)
        self.assertIn(ChannelsEpg.__tablename__, sql)


class FakeReadSessionContext:
    """记录测试读会话上下文的进入和退出次数。"""

    def __init__(self, session: object) -> None:
        """保存要返回给 Table 的测试 session。"""
        self.session = session
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> object:
        """进入读会话并返回固定 session。"""
        self.enter_count += 1
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """退出读会话并记录退出次数。"""
        self.exit_count += 1


class FakeDbManager:
    """为 SQLAlchemyTableView 生命周期测试提供可观察的 db_manager。"""

    def __init__(self, session: object | None = None) -> None:
        """初始化固定 session 和读会话上下文。"""
        self.session = session if session is not None else object()
        self.context = FakeReadSessionContext(self.session)
        self.read_session_calls = 0

    def get_read_session(self) -> FakeReadSessionContext:
        """返回同一个读会话上下文并记录调用次数。"""
        self.read_session_calls += 1
        return self.context


class SessionLifecycleSqlTable(SQLAlchemyTableView):
    """验证所有 SQLAlchemy Table 生命周期钩子共享同一 session 的表格。"""

    route_name = "session_lifecycle_table"
    route_path = "/session/table"
    model = EpgList
    columns = ["id"]

    def __init__(self) -> None:
        """初始化钩子调用记录。"""
        super().__init__()
        self.lifecycle_sessions: list[tuple[str, object | None]] = []

    def record_session(self, hook_name: str) -> None:
        """记录当前钩子看到的 db_session。"""
        self.lifecycle_sessions.append((hook_name, self.db_session))

    async def check_auth(self, request) -> bool:
        """记录鉴权阶段使用的 session。"""
        self.record_session("check_auth")
        return True

    async def get_queryset(self):
        """记录基础查询阶段使用的 session。"""
        self.record_session("get_queryset")
        return "query"

    async def apply_base_filters(self, query, table_request):
        """记录固定筛选阶段使用的 session。"""
        self.record_session("apply_base_filters")
        return query

    async def get_total_count(self, query) -> int:
        """记录统计阶段使用的 session。"""
        self.record_session("get_total_count")
        return 1

    async def apply_filters(self, query, table_request):
        """记录请求筛选阶段使用的 session。"""
        self.record_session("apply_filters")
        return query

    async def apply_search(self, query, table_request):
        """记录搜索阶段使用的 session。"""
        self.record_session("apply_search")
        return query

    async def apply_ordering(self, query, table_request):
        """记录排序阶段使用的 session。"""
        self.record_session("apply_ordering")
        return query

    async def paginate(self, query, table_request) -> list[dict[str, int]]:
        """记录分页阶段使用的 session。"""
        self.record_session("paginate")
        return [{"id": 1}]

    async def preload_record_data(self, row) -> dict[str, str]:
        """记录行预加载阶段使用的 session。"""
        self.record_session("preload_record_data")
        return {"state": "ready"}

    async def render_html_fragment(self, table_request, result) -> str:
        """记录 HTML 响应渲染阶段使用的 session。"""
        self.record_session("render_html_fragment")
        return '<div data-om-table-partial></div>'


class TableSqlAlchemySessionLifecycleTest(unittest.TestCase):
    """验证 SQLAlchemy Table 的数据库 session 生命周期。"""

    def test_get_uses_one_read_session_for_all_lifecycle_hooks(self) -> None:
        """一次请求应该只打开一个读 session，并贯穿所有查询和渲染钩子。"""
        manager = FakeDbManager()
        table = SessionLifecycleSqlTable()
        table.database_manager = cast(DatabaseManager, manager)

        response = asyncio.run(table.get(make_request()))

        self.assertEqual(response.status, 200)
        self.assertEqual(manager.read_session_calls, 1)
        self.assertEqual(manager.context.enter_count, 1)
        self.assertEqual(manager.context.exit_count, 1)
        self.assertIsNone(table.db_session)
        self.assertTrue(table.lifecycle_sessions)
        self.assertTrue(all(session is manager.session for _, session in table.lifecycle_sessions))
        self.assertEqual(
            [name for name, _ in table.lifecycle_sessions],
            [
                "check_auth",
                "get_queryset",
                "apply_base_filters",
                "get_total_count",
                "apply_filters",
                "apply_search",
                "get_total_count",
                "apply_ordering",
                "paginate",
                "preload_record_data",
                "render_html_fragment",
            ],
        )


class BusinessTableFilterTest(unittest.TestCase):
    """验证业务表格筛选错误边界。"""

    def test_channel_name_table_contract(self) -> None:
        """ChannelName 表格必须覆盖发布状态、绑定状态、排序和 action。"""
        from apps.epg_admin.tables import ChannelNameTable

        columns = column_declarations(ChannelNameTable)
        self.assertEqual(ChannelNameTable.route_path, "/channel-names/table")
        self.assertTrue(ChannelNameTable.selectable)
        self.assertIn("name", ChannelNameTable.search_fields)
        self.assertIn("name_cn", ChannelNameTable.search_fields)
        self.assertIn("name_tw", ChannelNameTable.search_fields)
        self.assertIn("name_es", ChannelNameTable.search_fields)
        self.assertIn(("Published", "published", "get_column_published_data"), columns)
        self.assertIn(("Bound EPG", "epg_id", "get_column_bound_epg_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_channel_name_service_uses_normal_attribute_assignment(self) -> None:
        """临时表单值必须继续经过对象的 __setattr__，不能直接改写 __dict__。"""
        source = (Path(__file__).resolve().parents[1] / "apps" / "epg_admin" / "services.py").read_text(encoding="utf-8")

        self.assertIn('setattr(channel_name, "_bound_epg", bound_epg)', source)
        self.assertNotIn('vars(channel_name)["_bound_epg"]', source)

    def test_channel_name_invalid_boolean_filter_raises_validation_error(self) -> None:
        """ChannelName 布尔筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import ChannelNameTable

        table = ChannelNameTable()
        request = table.build_table_request(make_request(args={"filter.published": "maybe"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_published(select(ChannelName), "maybe", request))

    def test_catalog_channel_table_contract(self) -> None:
        """CatalogChannel 表格必须覆盖状态、置信度、feed 计数和 action。"""
        from apps.epg_admin.tables import CatalogChannelTable

        columns = column_declarations(CatalogChannelTable)
        self.assertEqual(CatalogChannelTable.route_path, "/catalog-channels/table")
        self.assertTrue(CatalogChannelTable.selectable)
        self.assertIn("channel_key", CatalogChannelTable.search_fields)
        self.assertIn("channel_id", CatalogChannelTable.search_fields)
        self.assertIn("identity_name", CatalogChannelTable.search_fields)
        self.assertIn(("Status", "status", "get_column_status_data"), columns)
        self.assertIn(("Confidence", "confidence", "get_column_confidence_data"), columns)
        self.assertIn(("Feeds", None, "get_column_feed_count_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_catalog_channel_row_context_preserves_get_protocol(self) -> None:
        """Callback context may be any object implementing get(), as before extraction."""
        from apps.epg_admin.tables import CatalogChannelTable

        class RowContext:
            def get(self, key: str, default: object = None) -> object:
                return 7 if key == "feed_count" else default

        _display, raw_value = CatalogChannelTable().get_column_feed_count_data(
            cast(Any, object()),
            row_context=RowContext(),
        )

        self.assertEqual(raw_value, 7)

    def test_catalog_channel_confidence_filter_rejects_invalid_range(self) -> None:
        """CatalogChannel 置信度筛选非法范围必须返回表格校验错误。"""
        from apps.epg_admin.models import CatalogChannel
        from apps.epg_admin.tables import CatalogChannelTable

        table = CatalogChannelTable()
        request = table.build_table_request(make_request(args={"filter.confidence_min": "bad"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_confidence_min(select(CatalogChannel), "bad", request))

    def test_catalog_feed_table_contract(self) -> None:
        """CatalogFeed 表格必须覆盖状态、默认标记、logo 预览和 action。"""
        from apps.epg_admin.tables import CatalogFeedTable

        columns = column_declarations(CatalogFeedTable)
        self.assertEqual(CatalogFeedTable.route_path, "/catalog-feeds/table")
        self.assertTrue(CatalogFeedTable.selectable)
        self.assertIn("tvg_id", CatalogFeedTable.search_fields)
        self.assertIn("canonical_name", CatalogFeedTable.search_fields)
        self.assertIn("catalog_channel.identity_name", CatalogFeedTable.search_fields)
        self.assertIn(("Logo", None, "get_column_logo_data"), columns)
        self.assertIn(("Status", "status", "get_column_status_data"), columns)
        self.assertIn(("Default", "is_default", "get_column_is_default_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_catalog_feed_boolean_filter_rejects_invalid_value(self) -> None:
        """CatalogFeed 默认 feed 和 logo 布尔筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import CatalogFeedTable

        table = CatalogFeedTable()
        request = table.build_table_request(make_request(args={"filter.is_default": "maybe"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_is_default(select(CatalogFeed), "maybe", request))

    def test_catalog_feed_datetime_filter_rejects_invalid_value(self) -> None:
        """CatalogFeed 日期时间筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import CatalogFeedTable

        table = CatalogFeedTable()
        request = table.build_table_request(make_request(args={"filter.created_from": "not-a-time"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_created_from(select(CatalogFeed), "not-a-time", request))

    def test_notification_datetime_filter_normalizes_timezone_input(self) -> None:
        """通知时间筛选带时区时必须正常比较，不能因为 aware/naive 差异打出 500。"""
        from apps.epg_admin import services
        from apps.epg_admin.tables import NotificationTable

        table = NotificationTable()
        request = table.build_table_request(make_request(args={"filter.created_from": "2026-06-11T00:00:00+00:00"}), route_kwargs={})
        row = services.NotificationItem(
            id="decision:1",
            notification_type="decision",
            severity="critical",
            title="Manual review decision",
            description="Browser Gate Notification",
            created_at=dt.datetime(2026, 6, 11, 12, 0),
            href="/match-decisions",
            icon="ri-error-warning-line",
            tone="danger",
            source_label="Decision #1",
            source_model="CatalogMatchDecision",
            source_id=1,
        )

        rows = asyncio.run(table.filter_created_from([row], "2026-06-11T00:00:00+00:00", request))

        self.assertEqual(rows, [row])
        rows = asyncio.run(table.filter_created_to([row], "2026-06-11T23:00:00+00:00", request))
        self.assertEqual(rows, [row])

    def test_notification_invalid_datetime_filter_returns_table_422(self) -> None:
        """通知日期筛选非法值必须由 table endpoint 转成 422，而不是泄漏为 500。"""
        from apps.epg_admin.tables import NotificationTable

        class AuthenticatedNotificationTable(NotificationTable):
            """测试用通知表，绕过登录并避免数据库依赖。"""

            async def check_auth(self, request):
                """测试场景固定允许访问。"""
                return True

            async def get_object_list(self):
                """返回空列表，专门验证筛选错误响应。"""
                return []

        request = make_request(args={"filter.created_from": "bad-date"}, headers={"accept": "application/json"})

        response = asyncio.run(AuthenticatedNotificationTable().get(request))

        self.assertEqual(response.status, 422)

    def test_notification_table_uses_documented_realtime_window(self) -> None:
        """通知中心表格必须使用显式 Top-N 窗口，避免静默魔法数字。"""
        from apps.epg_admin import services
        from apps.epg_admin.tables import NotificationTable

        with patch("apps.epg_admin.tables.services.notification_items", new_callable=AsyncMock) as notification_items:
            notification_items.return_value = []

            rows = asyncio.run(NotificationTable().get_object_list())

        self.assertEqual(rows, [])
        notification_items.assert_awaited_once_with(limit=services.NOTIFICATION_CENTER_LIMIT)

    def test_catalog_feed_missing_logo_file_renders_placeholder(self) -> None:
        """CatalogFeed logo 路径不存在时必须渲染占位，不能输出会 404 的 img。"""
        from apps.epg_admin.tables import CatalogFeedTable

        row = SimpleNamespace(
            tvg_id="missing.logo",
            canonical_name="Missing Logo",
            logo_asset=SimpleNamespace(normalized_path="data/missing-logo.png"),
        )

        html, raw_value = CatalogFeedTable().get_column_logo_data(cast(Any, row))

        self.assertEqual(raw_value, "")
        self.assertIn("ri-image-line", str(html))
        self.assertNotIn("<img", str(html))

    def test_catalog_feed_existing_absolute_logo_path_renders_media_url(self) -> None:
        """CatalogFeed logo 绝对路径存在时必须转换为工作区内的 media 相对地址。"""
        from apps.epg_admin.tables import CatalogFeedTable

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            logo_path = Path(temp_dir) / "logo.png"
            logo_path.write_bytes(b"fake image")
            row = SimpleNamespace(
                tvg_id="existing.logo",
                canonical_name="Existing Logo",
                logo_asset=SimpleNamespace(normalized_path=str(logo_path)),
            )

            html, raw_value = CatalogFeedTable().get_column_logo_data(cast(Any, row))

        self.assertIn(f'/media/{logo_path.relative_to(Path.cwd()).as_posix()}', str(html))
        self.assertEqual(raw_value, str(logo_path))
        self.assertIn("<img", str(html))

    def test_upstream_record_table_contract(self) -> None:
        """UpstreamRecord 表格必须覆盖上游状态、feed 绑定、raw payload modal 和 action。"""
        from apps.epg_admin.tables import UpstreamRecordTable

        columns = column_declarations(UpstreamRecordTable)
        feed_column = next(column for column in UpstreamRecordTable().get_columns() if column.name == "catalog_feed.tvg_id")
        self.assertEqual(UpstreamRecordTable.route_path, "/upstream-records/table")
        self.assertTrue(UpstreamRecordTable.selectable)
        self.assertIn("source_code", UpstreamRecordTable.search_fields)
        self.assertIn("source_record_key", UpstreamRecordTable.search_fields)
        self.assertIn("primary_name", UpstreamRecordTable.search_fields)
        self.assertIn("catalog_feed.tvg_id", UpstreamRecordTable.search_fields)
        self.assertIn(("Source", "source_code", "get_column_source_code_data"), columns)
        self.assertIn(("Feed", "catalog_feed.tvg_id", "get_column_catalog_feed_data"), columns)
        self.assertIn(("Status", "status", "get_column_status_data"), columns)
        self.assertIn(("Last Seen", "last_seen_at", "get_column_last_seen_at_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)
        self.assertFalse(feed_column.sortable)

    def test_upstream_record_action_does_not_embed_raw_payload_in_table_fragment(self) -> None:
        """UpstreamRecord action 只能挂远程 modal，不能把完整 raw_payload 塞进表格 HTML。"""
        from apps.epg_admin.tables import UpstreamRecordTable

        raw_payload = "<script>alert(1)</script>" + ("x" * 12000)
        row = SimpleNamespace(id=7, raw_payload=raw_payload, source_record_key="dangerous-key")

        html, raw_value = UpstreamRecordTable().get_column_action_data(cast(Any, row))

        html_text = str(html)
        self.assertEqual(raw_value, "")
        self.assertIn('data-om-modal-target="#upstream-record-raw-modal"', html_text)
        self.assertIn('data-om-modal-url="/upstream-records/7/raw-modal"', html_text)
        self.assertNotIn(raw_payload, html_text)
        self.assertNotIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_text)

    def test_upstream_record_datetime_filter_rejects_invalid_value(self) -> None:
        """UpstreamRecord last_seen datetime 筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import UpstreamRecordTable

        table = UpstreamRecordTable()
        request = table.build_table_request(make_request(args={"filter.last_seen_from": "not-a-time"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_last_seen_from(select(UpstreamSourceRecord), "not-a-time", request))

    def test_upstream_record_catalog_feed_filter_rejects_invalid_value(self) -> None:
        """UpstreamRecord catalog_feed_id 筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import UpstreamRecordTable

        table = UpstreamRecordTable()
        request = table.build_table_request(make_request(args={"filter.catalog_feed_id": "not-an-id"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_catalog_feed_id(select(UpstreamSourceRecord), "not-an-id", request))

    def test_logo_asset_table_contract(self) -> None:
        """LogoAsset 表格必须覆盖缩略图、质量分、尺寸、hash 和远程对比 modal。"""
        from apps.epg_admin.tables import LogoAssetTable

        columns = column_declarations(LogoAssetTable)
        self.assertEqual(LogoAssetTable.route_path, "/logo-assets/table")
        self.assertTrue(LogoAssetTable.selectable)
        self.assertIn("catalog_feed.tvg_id", LogoAssetTable.search_fields)
        self.assertIn("catalog_feed.canonical_name", LogoAssetTable.search_fields)
        self.assertIn("sha256", LogoAssetTable.search_fields)
        self.assertIn(("Preview", None, "get_column_preview_data"), columns)
        self.assertIn(("Feed", "catalog_feed.tvg_id", "get_column_catalog_feed_data"), columns)
        self.assertIn(("Quality", "quality_score", "get_column_quality_score_data"), columns)
        self.assertIn(("Dimensions", "width", "get_column_dimensions_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_logo_asset_quality_filter_rejects_invalid_value(self) -> None:
        """LogoAsset quality_score 范围筛选非法值必须返回表格校验错误。"""
        from apps.epg_admin.tables import LogoAssetTable

        table = LogoAssetTable()
        request = table.build_table_request(make_request(args={"filter.quality_min": "bad"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_quality_min(select(CatalogLogoAsset), "bad", request))

    def test_logo_asset_missing_preview_renders_safe_placeholder(self) -> None:
        """LogoAsset 预览路径不存在时必须显示安全占位，不能输出坏 img。"""
        from apps.epg_admin.tables import LogoAssetTable

        row = SimpleNamespace(id=9, normalized_path="data/not-found.png", preview_path=None, catalog_feed=SimpleNamespace(tvg_id="demo.logo", canonical_name="Demo Logo"))

        html, raw_value = LogoAssetTable().get_column_preview_data(cast(Any, row))

        self.assertEqual(raw_value, "")
        self.assertIn("ri-image-line", str(html))
        self.assertNotIn("<img", str(html))

    def test_logo_asset_action_uses_remote_comparison_modal(self) -> None:
        """LogoAsset action 只能挂远程图片对比 modal，不在表格中内嵌多张大图。"""
        from apps.epg_admin.tables import LogoAssetTable

        row = SimpleNamespace(id=42)

        html, raw_value = LogoAssetTable().get_column_action_data(cast(Any, row))

        self.assertEqual(raw_value, "")
        self.assertIn('data-om-modal-target="#logo-asset-compare-modal"', str(html))
        self.assertIn('data-om-modal-url="/logo-assets/42/compare-modal"', str(html))

    def test_match_decision_table_contract(self) -> None:
        """MatchDecision 表格必须覆盖多关联字段、decision badge、reason、evidence 和 edit modal。"""
        from apps.epg_admin.tables import MatchDecisionTable

        columns = column_declarations(MatchDecisionTable)
        self.assertEqual(MatchDecisionTable.route_path, "/match-decisions/table")
        self.assertTrue(MatchDecisionTable.selectable)
        self.assertIn("decision_scope", MatchDecisionTable.search_fields)
        self.assertIn("reason", MatchDecisionTable.search_fields)
        self.assertIn("decided_by", MatchDecisionTable.search_fields)
        self.assertIn("catalog_feed.tvg_id", MatchDecisionTable.search_fields)
        self.assertIn(("Scope", "decision_scope", "get_column_decision_scope_data"), columns)
        self.assertIn(("Decision", "decision", "get_column_decision_data"), columns)
        self.assertIn(("Reason", "reason", "get_column_reason_data"), columns)
        self.assertIn(("Evidence", "evidence_json", "get_column_evidence_json_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_match_decision_filter_rejects_invalid_relation_ids(self) -> None:
        """MatchDecision 关联对象筛选非法 ID 必须返回表格校验错误。"""
        from apps.epg_admin.tables import MatchDecisionTable

        table = MatchDecisionTable()
        request = table.build_table_request(make_request(args={"filter.catalog_feed_id": "bad"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_catalog_feed_id(select(CatalogMatchDecision), "bad", request))

    def test_match_decision_table_supports_operator_filter(self) -> None:
        """MatchDecision 表格必须提供独立 operator/decided_by 筛选。"""
        from apps.epg_admin.tables import MatchDecisionTable

        table = MatchDecisionTable()
        request = table.build_table_request(make_request(args={"filter.decided_by": "alice"}), route_kwargs={})

        query = asyncio.run(table.filter_decided_by(select(CatalogMatchDecision), "alice", request))

        self.assertIn("catalog_match_decision.decided_by", str(query))
        self.assertIn("alice", str(query.compile(compile_kwargs={"literal_binds": True})))

    def test_match_decision_table_shell_outputs_initial_operator_filter(self) -> None:
        """MatchDecision 首屏 table shell 必须携带 URL 中的 operator 初始筛选。"""
        from apps.epg_admin.tables import MatchDecisionTable

        table = MatchDecisionTable(initial_filters={"decided_by": "browser-gate"})

        html = str(asyncio.run(table.render_shell()))

        self.assertIn('data-om-filter-decided-by="browser-gate"', html)

    def test_match_decision_action_uses_remote_modal(self) -> None:
        """MatchDecision action 必须打开远程普通 Modal 编辑表单。"""
        from apps.epg_admin.tables import MatchDecisionTable

        row = SimpleNamespace(id=13)

        html, raw_value = MatchDecisionTable().get_column_action_data(cast(Any, row))

        self.assertEqual(raw_value, "")
        self.assertIn('data-om-modal-target="#match-decision-edit-modal"', str(html))
        self.assertIn('data-om-modal-url="/match-decisions/13/edit-modal"', str(html))

    def test_epg_remote_modal_titles_escape_dynamic_html(self) -> None:
        """EPG 远程 modal 标题允许 HTML，但业务动态值必须 escape 后再拼入。"""
        source = (Path(__file__).resolve().parents[1] / "apps" / "epg_admin" / "views.py").read_text(encoding="utf-8")

        self.assertIn("key = escape(", source)
        self.assertIn("· {key}", source)
        self.assertIn('title = escape(', source)
        self.assertIn("· {title}", source)
        self.assertIn("escape(match_decision_label(decision))", source)

    def test_epg_list_invalid_channel_filter_raises_validation_error(self) -> None:
        """非法频道筛选值必须返回表格校验错误。"""
        from apps.epg_admin.tables import EpgListTable

        table = EpgListTable()
        request = table.build_table_request(make_request(args={"filter.channel_id": "abc"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_channel_id(select(EpgList), "abc", request))

    def test_epg_list_invalid_date_filter_raises_validation_error(self) -> None:
        """非法日期筛选值必须返回表格校验错误。"""
        from apps.epg_admin.tables import EpgListTable

        table = EpgListTable()
        request = table.build_table_request(make_request(args={"filter.date_from": "bad-date"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_date_from(select(EpgList), "bad-date", request))

    def test_relation_ordering_adds_explicit_join(self) -> None:
        """关系字段排序必须为关联表生成显式 JOIN。"""
        table = RelationSqlTable()
        request = table.build_table_request(make_request(args={"sort": "channel.name"}), route_kwargs={})

        query = asyncio.run(table.apply_ordering(select(EpgList), request))
        sql = str(query)

        self.assertIn("JOIN", sql)
        self.assertIn(ChannelsEpg.__tablename__, sql)

    def test_epg_list_relation_search_adds_explicit_join(self) -> None:
        """真实业务节目单表格必须支持频道名称关系字段搜索。"""
        from apps.epg_admin.tables import EpgListTable

        table = EpgListTable()
        request = table.build_table_request(make_request(args={"q": "BBC"}), route_kwargs={})

        query = asyncio.run(table.apply_search(select(EpgList), request))
        sql = str(query)

        self.assertIn("JOIN", sql)
        self.assertIn(ChannelsEpg.__tablename__, sql)

    def test_epg_list_relation_sort_adds_explicit_join(self) -> None:
        """真实业务节目单表格必须支持频道名称关系字段排序。"""
        from apps.epg_admin.tables import EpgListTable

        table = EpgListTable()
        request = table.build_table_request(make_request(args={"sort": "channel.name"}), route_kwargs={})

        query = asyncio.run(table.apply_ordering(select(EpgList), request))
        sql = str(query)

        self.assertIn("JOIN", sql)
        self.assertIn(ChannelsEpg.__tablename__, sql)

    def test_business_tables_expose_demo_table_capabilities(self) -> None:
        """业务 demo 表格应该展示多选、badge 和 action dropdown 能力。"""
        from apps.epg_admin.tables import ChannelsEpgTable

        table = ChannelsEpgTable()
        request = table.build_table_request(make_request(), route_kwargs={})
        result = TableResult(
            rows=[
                ChannelsEpg(
                    id=1,
                    name="BBC One",
                    src_url="https://example.test/epg.xml",
                    country="UK",
                    hits=42,
                    last_date=dt.date(2026, 6, 10),
                    create_date=dt.datetime(2026, 6, 10, 12, 0),
                )
            ],
            row_contexts=[{}],
            total=1,
            filtered_total=1,
        )

        html = str(asyncio.run(table.render_html_fragment(request, result)))

        self.assertIn("data-om-table-select-all", html)
        self.assertIn("data-om-table-select-row", html)
        self.assertIn("om-badge om-badge-default", html)
        self.assertIn("om-badge om-badge-info", html)
        self.assertIn("om-dropdown-menu om-dropdown-menu-end", html)
        self.assertIn("ri-more-fill", html)

    def test_sqlalchemy_loader_options_are_applied_by_table_lifecycle(self) -> None:
        """SQLAlchemyTableView 必须统一应用 loader_options，不能要求业务 get_queryset 手写 options。"""

        class LoaderOptionOnlyTable(SQLAlchemyTableView):
            """只声明 loader_options 的关系字段表格。"""

            route_name = "loader_option_table"
            route_path = "/loader-option/table"
            model = EpgList
            page_size = 3
            loader_options = [selectinload(EpgList.channel)]
            columns = [("Channel", "channel.name")]

            async def check_auth(self, request) -> bool:
                return True

            async def get_queryset(self):
                return select(EpgList)

        engine, session = make_epg_sqlite_session()
        counter = SqlStatementCounter(engine)
        try:
            table = LoaderOptionOnlyTable()
            table.database_manager = cast(DatabaseManager, FakeDbManager(SyncSessionAsyncAdapter(session)))
            response = asyncio.run(table.get(make_request(args={"page_size": "3"})))

            self.assertEqual(response.status, 200)
            self.assertLessEqual(counter.select_count, 4)
            self.assertIn(b"BBC One", response_body(response))
        finally:
            session.close()
            engine.dispose()

    def test_epg_list_table_renders_relation_page_without_n_plus_one_with_real_session(self) -> None:
        """真实业务关系列表页应在固定 SQL 数内渲染当前页，显示阶段不能 N+1。"""
        from apps.epg_admin.tables import EpgListTable

        class AuthenticatedEpgListTable(EpgListTable):
            """跳过登录依赖，保留业务表格查询和渲染路径。"""

            async def check_auth(self, request) -> bool:
                return True

        engine, session = make_epg_sqlite_session()
        counter = SqlStatementCounter(engine)
        try:
            table = AuthenticatedEpgListTable()
            table.database_manager = cast(DatabaseManager, FakeDbManager(SyncSessionAsyncAdapter(session)))
            response = asyncio.run(table.get(make_request(args={"page_size": "3"})))

            self.assertEqual(response.status, 200)
            self.assertLessEqual(counter.select_count, 4)
            body = response_body(response)
            self.assertIn(b"BBC One", body)
            self.assertIn(b"CNN", body)
        finally:
            session.close()
            engine.dispose()

    def test_epg_list_table_rejects_oversized_page_before_opening_session(self) -> None:
        """业务关系列表页必须保留 page_size 上限，非法请求不能进入数据库 session。"""
        from apps.epg_admin.tables import EpgListTable

        manager = FakeDbManager(object())
        table = EpgListTable()
        table.database_manager = cast(DatabaseManager, manager)

        response = asyncio.run(table.get(make_request(args={"page_size": "999"})))

        self.assertEqual(response.status, 400)
        self.assertEqual(manager.read_session_calls, 0)


class SyncSessionAsyncAdapter:
    """把同步 SQLAlchemy Session 适配成 Table 测试需要的 async execute 接口。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, query):
        """同步执行 SQLAlchemy 查询，并以 async 方法暴露给 Table 生命周期。"""
        return self.session.execute(query)


class SqlStatementCounter:
    """通过 SQLAlchemy engine event 统计真实 SELECT 语句数量。"""

    def __init__(self, engine) -> None:
        self.select_count = 0
        event.listen(engine, "before_cursor_execute", self.before_cursor_execute)

    def before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany) -> None:
        """统计 SELECT 语句，忽略建表和插入夹具数据。"""
        if statement.lstrip().upper().startswith("SELECT"):
            self.select_count += 1


def make_epg_sqlite_session() -> tuple[Engine, Session]:
    """创建真实 SQLite ORM session，并写入多条节目单关系数据。"""
    engine = create_engine("sqlite:///:memory:")
    ChannelsEpg.metadata.create_all(engine)
    seed_session = Session(engine)
    channels = [
        ChannelsEpg(id=1, name="BBC One", src_url="https://example.test/bbc", last_date=dt.date(2026, 6, 1), create_date=dt.datetime(2026, 6, 1, 12, 0)),
        ChannelsEpg(id=2, name="CNN", src_url="https://example.test/cnn", last_date=dt.date(2026, 6, 1), create_date=dt.datetime(2026, 6, 1, 12, 0)),
        ChannelsEpg(id=3, name="Al Jazeera", src_url="https://example.test/aljazeera", last_date=dt.date(2026, 6, 1), create_date=dt.datetime(2026, 6, 1, 12, 0)),
    ]
    programmes = [
        EpgList(id=1, channel_id=1, title="Morning News", description="Daily update", start_date=dt.datetime(2026, 6, 10, 9, 0), create_date=dt.datetime(2026, 6, 1, 12, 0)),
        EpgList(id=2, channel_id=2, title="World Report", description="Global news", start_date=dt.datetime(2026, 6, 10, 8, 0), create_date=dt.datetime(2026, 6, 1, 12, 0)),
        EpgList(id=3, channel_id=3, title="Market Watch", description="Business news", start_date=dt.datetime(2026, 6, 10, 7, 0), create_date=dt.datetime(2026, 6, 1, 12, 0)),
    ]
    seed_session.add_all([*channels, *programmes])
    seed_session.commit()
    seed_session.close()
    return engine, Session(engine)


def make_request(*, args: dict[str, str] | None = None, headers: dict[str, str] | None = None, app: Sanic | None = None):
    """构造 Table 单元测试需要的最小 request 对象。"""

    class Args(dict):
        """模拟 Sanic request.args 的 get 行为。"""

        def get(self, key, default=None):
            """读取单个查询参数。"""
            return super().get(key, default)

    class Headers(dict):
        """模拟 Sanic request.headers 的大小写无关读取。"""

        def get(self, key, default=None):
            """读取请求头。"""
            return super().get(key.lower(), default)

    return type(
        "RequestStub",
        (),
        {
            "args": Args(args or {}),
            "headers": Headers({(key or "").lower(): value for key, value in (headers or {}).items()}),
            "app": app,
        },
    )()


def make_sanic_args_request(*, args: dict[str, list[str]], headers: dict[str, str] | None = None):
    """构造更接近 Sanic request.args 的多值查询参数对象。"""

    class Args(dict):
        """模拟 Sanic request.args.items 返回 list 值。"""

        def get(self, key, default=None):
            """读取单个查询参数的首值。"""
            value = super().get(key, default)
            return value[0] if isinstance(value, list) and value else value

        def getlist(self, key):
            """读取同名查询参数的全部值。"""
            value = super().get(key, [])
            return value if isinstance(value, list) else [value]

    class Headers(dict):
        """模拟 Sanic request.headers 的大小写无关读取。"""

        def get(self, key, default=None):
            """读取请求头。"""
            return super().get(key.lower(), default)

    return type(
        "RequestStub",
        (),
        {
            "args": Args(args),
            "headers": Headers({(key or "").lower(): value for key, value in (headers or {}).items()}),
            "app": None,
        },
    )()
