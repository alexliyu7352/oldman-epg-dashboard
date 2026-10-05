"""EPG 业务表格测试；框架表格本身的行为由框架仓库的测试覆盖。"""

from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from apps.epg_admin.models import CatalogFeed, CatalogLogoAsset, CatalogMatchDecision, ChannelName, ChannelsEpg, EpgList, UpstreamSourceRecord
from sanic import Sanic
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oldman.db import DatabaseManager
from oldman.web.components.tables import BaseTableView, TableResult
from oldman.web.components.tables.views import TableValidationError


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

            async def check_auth(self, table_request):
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

    def test_upstream_record_search_keeps_records_without_a_feed(self) -> None:
        """搜索必须对 catalog_feed 用外连接，未绑定 feed 的上游记录不能被 JOIN 过滤掉。"""
        from apps.epg_admin.tables import UpstreamRecordTable

        table = UpstreamRecordTable()
        request = table.build_table_request(make_request(args={"q": "cctv"}), route_kwargs={})

        sql = str(asyncio.run(table.apply_search(select(UpstreamSourceRecord), request)))

        self.assertIn("LEFT OUTER JOIN", sql)
        self.assertIn(CatalogFeed.__tablename__, sql)

    def test_match_decision_search_keeps_decisions_without_a_feed(self) -> None:
        """人工决策搜索同样要保留没有关联 feed 的记录。"""
        from apps.epg_admin.tables import MatchDecisionTable

        table = MatchDecisionTable()
        request = table.build_table_request(make_request(args={"q": "manual"}), route_kwargs={})

        sql = str(asyncio.run(table.apply_search(select(CatalogMatchDecision), request)))

        self.assertIn("LEFT OUTER JOIN", sql)
        self.assertIn(CatalogFeed.__tablename__, sql)

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

    def test_epg_list_table_renders_relation_page_without_n_plus_one_with_real_session(self) -> None:
        """真实业务关系列表页应在固定 SQL 数内渲染当前页，显示阶段不能 N+1。"""
        from apps.epg_admin.tables import EpgListTable

        class AuthenticatedEpgListTable(EpgListTable):
            """跳过登录依赖，保留业务表格查询和渲染路径。"""

            async def check_auth(self, table_request) -> bool:
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
