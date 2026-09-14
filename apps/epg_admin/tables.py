"""EPG 后台 TableView 定义。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

from markupsafe import Markup, escape
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from apps.epg_admin import services
from apps.epg_admin.models import (
    CatalogChannel,
    CatalogFeed,
    CatalogLogoAsset,
    CatalogMatchDecision,
    ChannelName,
    ChannelsEpg,
    EpgList,
    UpstreamSourceRecord,
)
from oldman.i18n import gettext_lazy as _
from oldman.web.components.tables import BaseTableView, SQLAlchemyTableView, TableResult, TailwindTableRenderer
from oldman.web.components.tables.views import TableValidationError
from oldman.web.template import render_component_template_sync


def is_authenticated_request(request: Any) -> bool:
    """判断当前请求是否已经通过后台登录。"""
    session = getattr(getattr(request, "ctx", None), "session", None)
    return bool(session and session.is_authenticated())


def muted_truncate(value: str | None, *, max_width: int) -> Markup:
    """渲染 Tailwind/Oldman 风格的灰色截断文本。"""
    return Markup(
        '<span class="text-default-500 truncate inline-block" style="max-width: '
        f'{escape(max_width)}px;">{escape(value or "-")}</span>'
    )


def parse_filter_datetime(value: object) -> dt.datetime | None:
    """把筛选参数转换为日期时间，非法值返回 None。"""
    if value in {"", None}:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time.min)
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        # 数据库当前使用 naive datetime；带时区输入统一转成 naive UTC 再比较，避免筛选请求打出 500。
        return parsed.astimezone(dt.UTC).replace(tzinfo=None)
    return parsed


def parse_boolean_filter(value: object) -> bool:
    """解析表格布尔筛选值，非法值必须进入表格校验错误。"""

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise TableValidationError(_("Invalid boolean filter"))


def parse_confidence_filter(value: object) -> int:
    """解析 0-100 的置信度筛选值。"""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise TableValidationError(_("Invalid confidence filter")) from None
    if parsed < 0 or parsed > 100:
        raise TableValidationError(_("Invalid confidence filter"))
    return parsed


def parse_relation_filter_id(value: object, *, label: str) -> int:
    """解析远程选择器提交的关联 ID，非法值返回表格筛选错误。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise TableValidationError(str(_("Invalid %(label)s filter", label=label))) from None


class ChannelsEpgTable(SQLAlchemyTableView):
    """频道列表表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "channels_epg_table"
    route_path = "/channels-epg/table"
    model = ChannelsEpg
    page_size = 10
    selectable = True
    ordering = ["-id"]
    search_fields = ["name", "src_url", "country"]
    unsortable_columns = ["src_url", "action"]
    empty_message = _("No channels found.")
    columns = [
        (_("ID"), "id"),
        (_("Name"), "name"),
        (_("Source URL"), "src_url", "get_column_src_url_data"),
        (_("Country"), "country"),
        (_("Hits"), "hits"),
        (_("Last Date"), "last_date"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问频道表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回频道列表基础查询。"""
        return select(ChannelsEpg)

    def get_column_name_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道名称列。"""
        return Markup(f'<span class="font-medium">{escape(row.name)}</span>'), row.name

    def get_column_src_url_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道源地址截断显示值和完整 raw 值。"""
        return muted_truncate(row.src_url, max_width=320), row.src_url

    def get_column_country_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道国家 badge，并保留原始国家文本。"""
        country = row.country or str(_("Unknown"))
        return badge(country, tone="secondary"), country

    def get_column_hits_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道命中数 badge，并保留数值 raw value。"""
        return badge(str(row.hits), tone="info"), row.hits

    def get_column_last_date_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道最后同步日期。"""
        value, display = format_temporal_value(row.last_date, date_format="%d %b, %Y")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: ChannelsEpg, **kwargs: object):
        """渲染频道编辑动作。"""
        return action_dropdown(f"/channels-epg/{row.id}/edit"), ""


class EpgListTable(SQLAlchemyTableView):
    """节目单列表表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "epg_list_table"
    route_path = "/epg-list/table"
    model = EpgList
    page_size = 10
    selectable = True
    ordering = ["-start_date"]
    search_fields = ["title", "description", "channel.name"]
    loader_options = [selectinload(EpgList.channel)]
    unsortable_columns = ["description", "action"]
    empty_message = _("No programmes found.")
    columns = [
        (_("ID"), "id"),
        (_("Title"), "title", "get_column_title_data"),
        (_("Channel"), "channel.name", "get_column_channel_name_data"),
        (_("Start Time"), "start_date"),
        (_("Description"), "description", "get_column_description_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问节目单表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回节目单列表基础查询，关系预加载由 SQLAlchemyTableView 统一应用。"""
        return select(EpgList)

    async def filter_channel_id(self, query, value: object, table_request):
        """按频道 ID 筛选节目单。"""
        try:
            channel_id = int(str(value))
        except (TypeError, ValueError):
            raise TableValidationError(_("Invalid channel filter")) from None
        return query.where(EpgList.channel_id == channel_id)

    async def filter_date_from(self, query, value: object, table_request):
        """筛选开始时间不早于指定日期的节目单。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid start date filter"))
        return query.where(EpgList.start_date >= parsed) if parsed else query

    async def filter_date_to(self, query, value: object, table_request):
        """筛选开始时间不晚于指定日期的节目单。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid end date filter"))
        return query.where(EpgList.start_date <= parsed) if parsed else query

    def get_column_title_data(self, row: EpgList, **kwargs: object):
        """渲染节目标题链接，并返回标题与频道名组成的 raw 值。"""
        title = row.title or "-"
        channel_name = row.channel.name if row.channel else str(row.channel_id)
        raw_value = f"{title} {channel_name}"
        return action_link(f"/epg-list/{row.id}/edit", raw_value, class_name="font-medium"), raw_value

    def get_column_channel_name_data(self, row: EpgList, **kwargs: object):
        """渲染节目关联频道名称。"""
        channel_name = row.channel.name if row.channel else str(row.channel_id)
        return badge(channel_name, tone="secondary"), channel_name

    def get_column_start_date_data(self, row: EpgList, **kwargs: object):
        """渲染节目开始时间，并保留 ISO raw value 供前端排序使用。"""
        value, display = format_temporal_value(row.start_date, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="om-badge om-badge-default">{escape(display)}</span>'), value

    def get_column_description_data(self, row: EpgList, **kwargs: object):
        """渲染节目描述截断显示值和完整 raw 值。"""
        return muted_truncate(row.description, max_width=360), row.description or ""

    def get_column_action_data(self, row: EpgList, **kwargs: object):
        """渲染节目单编辑动作。"""
        return action_dropdown(f"/epg-list/{row.id}/edit"), ""


class ChannelNameTable(SQLAlchemyTableView):
    """频道名称列表表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "channel_names_table"
    route_path = "/channel-names/table"
    model = ChannelName
    page_size = 10
    selectable = True
    ordering = ["channel_order", "name"]
    search_fields = ["name", "name_cn", "name_tw", "name_es", "logo"]
    unsortable_columns = ["action"]
    empty_message = _("No channel names found.")
    columns = [
        (_("ID"), "id"),
        (_("Name"), "name", "get_column_name_data"),
        (_("Chinese"), "name_cn", "get_column_name_cn_data"),
        (_("Traditional"), "name_tw", "get_column_name_tw_data"),
        (_("Spanish"), "name_es", "get_column_name_es_data"),
        (_("Published"), "published", "get_column_published_data"),
        (_("Bound EPG"), "epg_id", "get_column_bound_epg_data"),
        (_("Country"), "country_id"),
        (_("Language"), "language_id"),
        (_("Order"), "channel_order"),
        (_("Logo Size"), "logo_size"),
        (_("Last Date"), "last_date", "get_column_last_date_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问频道名称表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回频道名称列表基础查询。"""
        return select(ChannelName)

    async def filter_published(self, query, value: object, table_request):
        """按发布状态筛选频道名称。"""
        return query.where(ChannelName.published.is_(parse_boolean_filter(value)))

    async def filter_bound_epg(self, query, value: object, table_request):
        """按是否绑定 ChannelsEpg 筛选频道名称。"""
        if parse_boolean_filter(value):
            return query.where(ChannelName.epg_id.is_not(None))
        return query.where(ChannelName.epg_id.is_(None))

    async def filter_country_id(self, query, value: object, table_request):
        """按国家 ID 筛选频道名称。"""
        try:
            country_id = int(str(value).strip())
        except (TypeError, ValueError):
            raise TableValidationError(_("Invalid country filter")) from None
        return query.where(ChannelName.country_id == country_id)

    async def filter_language_id(self, query, value: object, table_request):
        """按语言 ID 筛选频道名称。"""
        try:
            language_id = int(str(value).strip())
        except (TypeError, ValueError):
            raise TableValidationError(_("Invalid language filter")) from None
        return query.where(ChannelName.language_id == language_id)

    def get_column_name_data(self, row: ChannelName, **kwargs: object):
        """渲染频道名称主列，并附带 logo 标识。"""
        name = row.name or "-"
        logo = row.logo or ""
        if logo:
            return (
                Markup(
                    f'<a href="/channel-names/{row.id}/edit" class="link-primary font-medium">{escape(name)}</a>'
                    f'<div class="text-default-500 text-xs">{escape(logo)}</div>'
                ),
                f"{name} {logo}",
            )
        return action_link(f"/channel-names/{row.id}/edit", name, class_name="font-medium"), name

    def get_column_name_cn_data(self, row: ChannelName, **kwargs: object):
        """渲染简体中文名称。"""
        return muted_truncate(row.name_cn, max_width=160), row.name_cn or ""

    def get_column_name_tw_data(self, row: ChannelName, **kwargs: object):
        """渲染繁体中文名称。"""
        return muted_truncate(row.name_tw, max_width=160), row.name_tw or ""

    def get_column_name_es_data(self, row: ChannelName, **kwargs: object):
        """渲染西班牙语名称。"""
        return muted_truncate(row.name_es, max_width=160), row.name_es or ""

    def get_column_published_data(self, row: ChannelName, **kwargs: object):
        """渲染发布状态 badge。"""
        if row.published:
            return badge(str(_("Published")), tone="success"), "Published"
        return badge(str(_("Draft")), tone="secondary"), "Draft"

    def get_column_bound_epg_data(self, row: ChannelName, **kwargs: object):
        """渲染 EPG 绑定状态 badge。"""
        if row.epg_id:
            return badge(f"EPG #{row.epg_id}", tone="info"), row.epg_id
        return badge(str(_("Unbound")), tone="warning"), ""

    def get_column_last_date_data(self, row: ChannelName, **kwargs: object):
        """渲染频道名称最后更新时间。"""
        value, display = format_temporal_value(row.last_date, date_format="%d %b, %Y")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: ChannelName, **kwargs: object):
        """渲染频道名称编辑动作。"""
        return action_dropdown(f"/channel-names/{row.id}/edit"), ""


class CatalogChannelTable(SQLAlchemyTableView):
    """频道目录列表表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "catalog_channels_table"
    route_path = "/catalog-channels/table"
    model = CatalogChannel
    page_size = 10
    selectable = True
    ordering = ["-updated_at"]
    search_fields = ["channel_key", "channel_id", "identity_name", "owner_country_code", "status"]
    unsortable_columns = ["feeds", "action"]
    empty_message = _("No catalog channels found.")
    columns = [
        (_("Channel Key"), "channel_key", "get_column_channel_key_data"),
        (_("Identity"), "identity_name", "get_column_identity_name_data"),
        (_("Owner Country"), "owner_country_code", "get_column_owner_country_code_data"),
        (_("Status"), "status", "get_column_status_data"),
        (_("Confidence"), "confidence", "get_column_confidence_data"),
        (_("Feeds"), None, "get_column_feed_count_data"),
        (_("Updated"), "updated_at", "get_column_updated_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问频道目录表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回频道目录列表基础查询。"""
        return select(CatalogChannel)

    async def query_result(self, table_request):
        """执行频道目录查询，并一次聚合当前页 feed 数量。"""
        query = await self.get_queryset()
        query = self.apply_loader_options(query)
        query = await self.apply_base_filters(query, table_request)
        total = await self.get_total_count(query)
        filtered_query = await self.apply_filters(query, table_request)
        filtered_query = await self.apply_search(filtered_query, table_request)
        filtered_total = await self.get_total_count(filtered_query)
        ordered_query = await self.apply_ordering(filtered_query, table_request)
        rows = cast(list[CatalogChannel], await self.paginate(ordered_query, table_request))
        counts = await services.catalog_channel_feed_counts([int(row.id) for row in rows], session=self.require_db_session())
        row_contexts: list[dict[str, object]] = [{"feed_count": counts.get(int(row.id), 0)} for row in rows]
        return TableResult(rows=rows, row_contexts=row_contexts, total=total, filtered_total=filtered_total, page=table_request.page, page_size=table_request.page_size)

    async def filter_status(self, query, value: object, table_request):
        """按频道目录状态筛选。"""
        value_text = str(value).strip()
        if not value_text:
            return query
        return query.where(CatalogChannel.status == value_text)

    async def filter_owner_country_code(self, query, value: object, table_request):
        """按频道所有者国家筛选。"""
        value_text = str(value).strip()
        if not value_text:
            return query
        return query.where(CatalogChannel.owner_country_code == value_text)

    async def filter_confidence_min(self, query, value: object, table_request):
        """筛选置信度不低于指定值的频道目录。"""
        return query.where(CatalogChannel.confidence >= parse_confidence_filter(value))

    async def filter_confidence_max(self, query, value: object, table_request):
        """筛选置信度不高于指定值的频道目录。"""
        return query.where(CatalogChannel.confidence <= parse_confidence_filter(value))

    def get_column_channel_key_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道目录 key 和 channel_id。"""
        return (
            Markup(
                f'<a href="/catalog-channels/{row.id}/edit" class="link-primary font-medium">{escape(row.channel_key)}</a>'
                f'<div class="text-default-500 text-xs">{escape(row.channel_id)}</div>'
            ),
            f"{row.channel_key} {row.channel_id}",
        )

    def get_column_identity_name_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道身份名称。"""
        return muted_truncate(row.identity_name, max_width=260), row.identity_name

    def get_column_owner_country_code_data(self, row: CatalogChannel, **kwargs: object):
        """渲染所有者国家 badge。"""
        return badge(row.owner_country_code or str(_("Unknown")), tone="secondary"), row.owner_country_code

    def get_column_status_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道目录状态 badge。"""
        tones = {
            "accepted": "success",
            "rejected": "danger",
            "manual_review": "warning",
            "provisional": "info",
        }
        return badge(str(_(row.status.replace("_", " ").title())), tone=tones.get(row.status, "secondary")), row.status

    def get_column_confidence_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道目录置信度进度条。"""
        value = max(0, min(100, int(row.confidence or 0)))
        tone = "success" if value >= 80 else "warning" if value >= 50 else "danger"
        bar_class = {
            "success": "bg-green-500",
            "warning": "bg-amber-500",
            "danger": "bg-red-500",
        }[tone]
        return (
            Markup(
                '<div class="flex items-center gap-2">'
                '<div class="h-1.5 flex-1 overflow-hidden rounded-full bg-default-100">'
                f'<div class="h-full rounded-full {bar_class}" role="progressbar" style="width: {value}%;" aria-valuenow="{value}" aria-valuemin="0" aria-valuemax="100"></div>'
                "</div>"
                f'<span class="text-default-500 text-xs">{value}%</span>'
                "</div>"
            ),
            value,
        )

    def get_column_feed_count_data(self, row: CatalogChannel, **kwargs: object):
        """渲染关联 feed 数量，数据来自当前页批量聚合上下文。"""
        row_context = cast(Any, kwargs.get("row_context") or {})
        count = int(row_context.get("feed_count", 0) or 0)
        return badge(str(count), tone="primary"), count

    def get_column_updated_at_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道目录更新时间。"""
        value, display = format_temporal_value(row.updated_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: CatalogChannel, **kwargs: object):
        """渲染频道目录编辑和证据预览动作。"""
        safe_modal_id = f"catalog-channel-evidence-{int(row.id)}"
        evidence_body = Markup('<pre class="mb-0 text-xs text-default-500">') + escape(row.evidence_json or "{}") + Markup("</pre>")
        evidence_modal = render_component_template_sync(
            self,
            "oldman/dashboard/components/modal_fragment.html",
            {
                "modal_id": safe_modal_id,
                "modal_title": _("Evidence JSON"),
                "modal_component": "modal",
                "modal_close_label": _("Close"),
                "modal_managed": False,
                "modal_footer_close_label": _("Close"),
                "modal_dialog_class": "om-modal-dialog-lg",
                "modal_hidden": False,
                "modal_body": evidence_body,
            },
        )
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><a class="om-dropdown-item" href="/catalog-channels/{row.id}/edit"><i class="ri-pencil-fill align-bottom mr-2 text-default-500"></i>{escape(_("Edit"))}</a></li>'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#{safe_modal_id}"><i class="ri-file-search-line align-bottom mr-2 text-default-500"></i>{escape(_("Evidence"))}</button></li>'
                "</ul>"
                "</div>"
                f"{evidence_modal}"
            ),
            "",
        )


class CatalogFeedTable(SQLAlchemyTableView):
    """CatalogFeed 列表表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "catalog_feeds_table"
    route_path = "/catalog-feeds/table"
    model = CatalogFeed
    page_size = 10
    selectable = True
    ordering = ["-updated_at"]
    search_fields = [
        "tvg_id",
        "canonical_name",
        "service_country_code",
        "region_code",
        "language_hints_text",
        "timezone_hints_text",
        "version_kind",
        "status",
        "catalog_channel.identity_name",
        "catalog_channel.channel_id",
    ]
    loader_options = [selectinload(CatalogFeed.catalog_channel), selectinload(CatalogFeed.logo_asset)]
    unsortable_columns = ["logo", "language", "timezone", "action"]
    empty_message = _("No catalog feeds found.")
    columns = [
        (_("Feed"), "tvg_id", "get_column_tvg_id_data"),
        (_("Logo"), None, "get_column_logo_data"),
        (_("Catalog Channel"), "catalog_channel.identity_name", "get_column_catalog_channel_data"),
        (_("Country"), "service_country_code", "get_column_service_country_code_data"),
        (_("Region"), "region_code", "get_column_region_code_data"),
        (_("Language"), "language_hints_text", "get_column_language_hints_text_data"),
        (_("Timezone"), "timezone_hints_text", "get_column_timezone_hints_text_data"),
        (_("Version"), "version_kind", "get_column_version_kind_data"),
        (_("Status"), "status", "get_column_status_data"),
        (_("Default"), "is_default", "get_column_is_default_data"),
        (_("Updated"), "updated_at", "get_column_updated_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问 feed 表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回 feed 列表基础查询。"""
        return select(CatalogFeed)

    async def filter_status(self, query, value: object, table_request):
        """按 feed 状态筛选。"""
        value_text = str(value).strip()
        return query.where(CatalogFeed.status == value_text) if value_text else query

    async def filter_service_country_code(self, query, value: object, table_request):
        """按服务国家筛选 feed。"""
        value_text = str(value).strip()
        return query.where(CatalogFeed.service_country_code == value_text) if value_text else query

    async def filter_version_kind(self, query, value: object, table_request):
        """按版本类型筛选 feed。"""
        value_text = str(value).strip()
        return query.where(CatalogFeed.version_kind == value_text) if value_text else query

    async def filter_is_default(self, query, value: object, table_request):
        """按是否默认 feed 筛选。"""
        return query.where(CatalogFeed.is_default.is_(parse_boolean_filter(value)))

    async def filter_has_logo(self, query, value: object, table_request):
        """按是否存在 CatalogLogoAsset 筛选 feed。"""
        if parse_boolean_filter(value):
            return query.where(CatalogFeed.logo_asset.has())
        return query.where(~CatalogFeed.logo_asset.has())

    async def filter_created_from(self, query, value: object, table_request):
        """筛选创建时间不早于指定 datetime 的 feed。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid created from filter"))
        return query.where(CatalogFeed.created_at >= parsed) if parsed else query

    async def filter_created_to(self, query, value: object, table_request):
        """筛选创建时间不晚于指定 datetime 的 feed。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid created to filter"))
        return query.where(CatalogFeed.created_at <= parsed) if parsed else query

    async def filter_updated_from(self, query, value: object, table_request):
        """筛选更新时间不早于指定 datetime 的 feed。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid updated from filter"))
        return query.where(CatalogFeed.updated_at >= parsed) if parsed else query

    async def filter_updated_to(self, query, value: object, table_request):
        """筛选更新时间不晚于指定 datetime 的 feed。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid updated to filter"))
        return query.where(CatalogFeed.updated_at <= parsed) if parsed else query

    def get_column_tvg_id_data(self, row: CatalogFeed, **kwargs: object):
        """渲染 feed 主列，显示 TVG ID 和规范名称。"""
        tvg_id = row.tvg_id or "-"
        canonical_name = row.canonical_name or "-"
        return (
            Markup(
                f'<a href="/catalog-feeds/{row.id}/edit" class="link-primary font-medium">{escape(tvg_id)}</a>'
                f'<div class="text-default-500 text-xs">{escape(canonical_name)}</div>'
            ),
            f"{tvg_id} {canonical_name}",
        )

    def get_column_logo_data(self, row: CatalogFeed, **kwargs: object):
        """渲染 logo 预览；没有资产时保留可见占位。"""
        logo_asset = getattr(row, "logo_asset", None)
        logo_path = first_logo_path(logo_asset)
        label = row.canonical_name or row.tvg_id or "Logo"
        logo_src = resolve_logo_src(logo_path) if logo_path else ""
        if logo_src:
            return (
                Markup(
                    '<div class="om-avatar-xs">'
                    f'<img src="{escape(logo_src)}" alt="{escape(label)}" class="size-full rounded object-contain bg-default-50 border">'
                    "</div>"
                ),
                logo_path,
            )
        return (
            Markup(
                '<div class="om-avatar-xs"><span class="om-avatar-title rounded bg-default-50 text-default-500 border">'
                '<i class="ri-image-line"></i>'
                "</span></div>"
            ),
            "",
        )

    def get_column_catalog_channel_data(self, row: CatalogFeed, **kwargs: object):
        """渲染关联 CatalogChannel 的身份名称和 channel_id。"""
        catalog_channel = getattr(row, "catalog_channel", None)
        if catalog_channel is None:
            return badge(str(_("Missing")), tone="warning"), ""
        identity = catalog_channel.identity_name or catalog_channel.channel_key or str(catalog_channel.id)
        channel_id = catalog_channel.channel_id or catalog_channel.channel_key or str(catalog_channel.id)
        return (
            Markup(f'<span class="font-medium">{escape(identity)}</span><div class="text-default-500 text-xs">{escape(channel_id)}</div>'),
            f"{identity} {channel_id}",
        )

    def get_column_service_country_code_data(self, row: CatalogFeed, **kwargs: object):
        """渲染服务国家 badge。"""
        return badge(row.service_country_code or str(_("Unknown")), tone="secondary"), row.service_country_code or ""

    def get_column_region_code_data(self, row: CatalogFeed, **kwargs: object):
        """渲染地区代码。"""
        return muted_truncate(row.region_code, max_width=120), row.region_code or ""

    def get_column_language_hints_text_data(self, row: CatalogFeed, **kwargs: object):
        """渲染语言提示截断文本。"""
        return muted_truncate(row.language_hints_text, max_width=160), row.language_hints_text or ""

    def get_column_timezone_hints_text_data(self, row: CatalogFeed, **kwargs: object):
        """渲染时区提示截断文本。"""
        return muted_truncate(row.timezone_hints_text, max_width=160), row.timezone_hints_text or ""

    def get_column_version_kind_data(self, row: CatalogFeed, **kwargs: object):
        """渲染版本类型 badge。"""
        return badge(row.version_kind or "default", tone="info"), row.version_kind or ""

    def get_column_status_data(self, row: CatalogFeed, **kwargs: object):
        """渲染 feed 状态 badge。"""
        tones = {
            "accepted": "success",
            "rejected": "danger",
            "manual_review": "warning",
            "provisional": "info",
            "retired": "secondary",
        }
        return badge(str(_(row.status.replace("_", " ").title())), tone=tones.get(row.status, "secondary")), row.status

    def get_column_is_default_data(self, row: CatalogFeed, **kwargs: object):
        """渲染默认 feed 标记。"""
        if row.is_default:
            return badge(str(_("Default")), tone="success"), "Default"
        return badge(str(_("Variant")), tone="secondary"), "Variant"

    def get_column_updated_at_data(self, row: CatalogFeed, **kwargs: object):
        """渲染 feed 更新时间。"""
        value, display = format_temporal_value(row.updated_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: CatalogFeed, **kwargs: object):
        """渲染 feed 编辑和 logo 说明动作。"""
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><a class="om-dropdown-item" href="/catalog-feeds/{row.id}/edit"><i class="ri-pencil-fill align-bottom mr-2 text-default-500"></i>{escape(_("Edit"))}</a></li>'
                f'<li><button class="om-dropdown-item" type="button" data-om-feedback-message="{escape(_("Logo upload preview is handled on the edit page."))}"><i class="ri-image-edit-line align-bottom mr-2 text-default-500"></i>{escape(_("Logo Preview"))}</button></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )


class UpstreamRecordTable(SQLAlchemyTableView):
    """上游原始记录审计表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "upstream_records_table"
    route_path = "/upstream-records/table"
    model = UpstreamSourceRecord
    page_size = 10
    selectable = True
    ordering = ["-last_seen_at"]
    search_fields = ["source_code", "source_record_key", "primary_name", "raw_hash", "catalog_feed.tvg_id", "catalog_feed.canonical_name"]
    loader_options = [selectinload(UpstreamSourceRecord.catalog_feed)]
    unsortable_columns = ["catalog_feed.tvg_id", "payload", "action"]
    empty_message = _("No upstream records found.")
    columns = [
        (_("Source"), "source_code", "get_column_source_code_data"),
        (_("Record Key"), "source_record_key", "get_column_source_record_key_data"),
        (_("Kind"), "record_kind", "get_column_record_kind_data"),
        (_("Primary Name"), "primary_name", "get_column_primary_name_data"),
        (_("Feed"), "catalog_feed.tvg_id", "get_column_catalog_feed_data"),
        (_("Status"), "status", "get_column_status_data"),
        (_("Hash"), "raw_hash", "get_column_raw_hash_data"),
        (_("Last Seen"), "last_seen_at", "get_column_last_seen_at_data"),
        (_("Disappeared"), "disappeared_at", "get_column_disappeared_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问上游记录表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回上游记录列表基础查询。"""
        return select(UpstreamSourceRecord)

    async def apply_search(self, query, table_request):
        """使用外连接搜索 feed 字段，避免未绑定记录被 inner join 过滤掉。"""
        search = table_request.q.strip()
        if not search:
            return query
        like = f"%{search}%"
        return query.outerjoin(UpstreamSourceRecord.catalog_feed).where(
            or_(
                UpstreamSourceRecord.source_code.like(like),
                UpstreamSourceRecord.source_record_key.like(like),
                UpstreamSourceRecord.primary_name.like(like),
                UpstreamSourceRecord.raw_hash.like(like),
                CatalogFeed.tvg_id.like(like),
                CatalogFeed.canonical_name.like(like),
            )
        )

    async def filter_source_code(self, query, value: object, table_request):
        """按上游来源筛选记录。"""
        value_text = str(value).strip()
        return query.where(UpstreamSourceRecord.source_code == value_text) if value_text else query

    async def filter_status(self, query, value: object, table_request):
        """按记录状态筛选。"""
        value_text = str(value).strip()
        return query.where(UpstreamSourceRecord.status == value_text) if value_text else query

    async def filter_record_kind(self, query, value: object, table_request):
        """按记录类型筛选。"""
        value_text = str(value).strip()
        return query.where(UpstreamSourceRecord.record_kind == value_text) if value_text else query

    async def filter_catalog_feed_id(self, query, value: object, table_request):
        """按绑定的 CatalogFeed ID 筛选。"""
        try:
            feed_id = int(str(value).strip())
        except (TypeError, ValueError):
            raise TableValidationError(_("Invalid catalog feed filter")) from None
        return query.where(UpstreamSourceRecord.catalog_feed_id == feed_id)

    async def filter_last_seen_from(self, query, value: object, table_request):
        """筛选最后发现时间不早于指定 datetime 的上游记录。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid last seen from filter"))
        return query.where(UpstreamSourceRecord.last_seen_at >= parsed) if parsed else query

    async def filter_last_seen_to(self, query, value: object, table_request):
        """筛选最后发现时间不晚于指定 datetime 的上游记录。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid last seen to filter"))
        return query.where(UpstreamSourceRecord.last_seen_at <= parsed) if parsed else query

    def get_column_source_code_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游来源和 URL 摘要。"""
        source = row.source_code or "-"
        source_url = row.source_url or ""
        if source_url:
            return (
                Markup(
                    f'{badge(source, tone="info")}'
                    f'<div class="text-default-500 text-xs truncate" style="max-width: 180px;">{escape(source_url)}</div>'
                ),
                f"{source} {source_url}",
            )
        return badge(source, tone="info"), source

    def get_column_source_record_key_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游原始 key。"""
        return muted_truncate(row.source_record_key, max_width=260), row.source_record_key or ""

    def get_column_record_kind_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游记录类型。"""
        return badge(row.record_kind or "unknown", tone="secondary"), row.record_kind or ""

    def get_column_primary_name_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游主名称。"""
        return muted_truncate(row.primary_name, max_width=240), row.primary_name or ""

    def get_column_catalog_feed_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游记录绑定的 CatalogFeed。"""
        feed = getattr(row, "catalog_feed", None)
        if feed is None:
            return badge(str(_("Unbound")), tone="warning"), ""
        tvg_id = feed.tvg_id or str(feed.id)
        name = feed.canonical_name or tvg_id
        return (
            Markup(
                f'<a href="/catalog-feeds/{feed.id}/edit" class="link-primary font-medium">{escape(tvg_id)}</a>'
                f'<div class="text-default-500 text-xs">{escape(name)}</div>'
            ),
            f"{tvg_id} {name}",
        )

    def get_column_status_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染上游记录状态 badge。"""
        tones = {
            "active": "success",
            "disappeared": "warning",
            "stale": "secondary",
            "ignored": "secondary",
            "error": "danger",
        }
        status = row.status or "unknown"
        return badge(str(_(status.replace("_", " ").title())), tone=tones.get(row.status, "secondary")), row.status or ""

    def get_column_raw_hash_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染 raw payload hash 截断值。"""
        return muted_truncate(row.raw_hash, max_width=160), row.raw_hash or ""

    def get_column_last_seen_at_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染最后发现时间。"""
        value, display = format_temporal_value(row.last_seen_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_disappeared_at_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染消失时间，仍在线时显示 active badge。"""
        if not row.disappeared_at:
            return badge(str(_("Visible")), tone="success"), ""
        value, display = format_temporal_value(row.disappeared_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-amber-700">{escape(display)}</span>'), value

    def get_column_action_data(self, row: UpstreamSourceRecord, **kwargs: object):
        """渲染 raw payload 远程预览动作，避免表格片段内嵌大 payload。"""
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#upstream-record-raw-modal" data-om-modal-url="/upstream-records/{int(row.id)}/raw-modal"><i class="ri-file-search-line align-bottom mr-2 text-default-500"></i>{escape(_("Raw Payload"))}</button></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )


class LogoAssetTable(SQLAlchemyTableView):
    """Logo 资产质量工作台表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "logo_assets_table"
    route_path = "/logo-assets/table"
    model = CatalogLogoAsset
    page_size = 10
    selectable = True
    ordering = ["quality_score"]
    search_fields = ["catalog_feed.tvg_id", "catalog_feed.canonical_name", "source_kind", "mime_type", "sha256", "phash", "dhash"]
    loader_options = [selectinload(CatalogLogoAsset.catalog_feed)]
    unsortable_columns = ["preview", "hashes", "action"]
    empty_message = _("No logo assets found.")
    columns = [
        (_("Preview"), None, "get_column_preview_data"),
        (_("Feed"), "catalog_feed.tvg_id", "get_column_catalog_feed_data"),
        (_("Source"), "source_kind", "get_column_source_kind_data"),
        (_("Mime"), "mime_type", "get_column_mime_type_data"),
        (_("Quality"), "quality_score", "get_column_quality_score_data"),
        (_("Dimensions"), "width", "get_column_dimensions_data"),
        (_("Hashes"), None, "get_column_hashes_data"),
        (_("Updated"), "updated_at", "get_column_updated_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问 Logo 资产表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回 LogoAsset 列表基础查询。"""
        return select(CatalogLogoAsset)

    async def filter_catalog_feed_id(self, query, value: object, table_request):
        """按绑定的 CatalogFeed ID 筛选 Logo 资产。"""
        try:
            feed_id = int(str(value).strip())
        except (TypeError, ValueError):
            raise TableValidationError(_("Invalid catalog feed filter")) from None
        return query.where(CatalogLogoAsset.catalog_feed_id == feed_id)

    async def filter_source_kind(self, query, value: object, table_request):
        """按 Logo 来源类型筛选。"""
        value_text = str(value).strip()
        return query.where(CatalogLogoAsset.source_kind == value_text) if value_text else query

    async def filter_mime_type(self, query, value: object, table_request):
        """按图片 MIME 类型筛选。"""
        value_text = str(value).strip()
        return query.where(CatalogLogoAsset.mime_type == value_text) if value_text else query

    async def filter_quality_min(self, query, value: object, table_request):
        """筛选质量分不低于指定值的 Logo 资产。"""
        score = parse_score_filter(value, label=str(_("Quality Min")))
        return query.where(CatalogLogoAsset.quality_score >= score)

    async def filter_quality_max(self, query, value: object, table_request):
        """筛选质量分不高于指定值的 Logo 资产。"""
        score = parse_score_filter(value, label=str(_("Quality Max")))
        return query.where(CatalogLogoAsset.quality_score <= score)

    def get_column_preview_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染 Logo 缩略图，路径不可用时显示稳定占位。"""
        logo_path = first_logo_path(row)
        label = logo_alt_text(row)
        logo_src = resolve_logo_src(logo_path) if logo_path else ""
        if logo_src:
            return (
                Markup(
                    '<div class="om-avatar-sm">'
                    f'<img src="{escape(logo_src)}" alt="{escape(label)}" class="size-full rounded object-contain bg-default-50 border" loading="lazy">'
                    "</div>"
                ),
                logo_path,
            )
        return image_placeholder()

    def get_column_catalog_feed_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染 LogoAsset 绑定的 CatalogFeed。"""
        feed = getattr(row, "catalog_feed", None)
        if feed is None:
            return badge(str(_("Missing")), tone="warning"), ""
        tvg_id = feed.tvg_id or str(feed.id)
        name = feed.canonical_name or tvg_id
        return (
            Markup(
                f'<a href="/catalog-feeds/{feed.id}/edit" class="link-primary font-medium">{escape(tvg_id)}</a>'
                f'<div class="text-default-500 text-xs">{escape(name)}</div>'
            ),
            f"{tvg_id} {name}",
        )

    def get_column_source_kind_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染 Logo 来源类型。"""
        return badge(row.source_kind or "unknown", tone="info"), row.source_kind or ""

    def get_column_mime_type_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染图片 MIME 类型。"""
        return badge(row.mime_type or "unknown", tone="secondary"), row.mime_type or ""

    def get_column_quality_score_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染质量分 badge。"""
        score = int(row.quality_score or 0)
        tone = "success" if score >= 70 else "warning" if score >= 40 else "danger"
        return badge(str(score), tone=tone), score

    def get_column_dimensions_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染图片尺寸，raw 值使用宽度以支持列排序。"""
        width = int(row.width or 0)
        height = int(row.height or 0)
        return Markup(f'<span class="font-medium">{width} x {height}</span>'), width

    def get_column_hashes_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染 sha256/phash 摘要。"""
        sha = row.sha256 or ""
        phash = row.phash or row.dhash or ""
        return (
            Markup(
                f'<div class="text-default-500 text-xs truncate" style="max-width: 180px;">sha {escape(sha[:16] or "-")}</div>'
                f'<div class="text-default-500 text-xs truncate" style="max-width: 180px;">phash {escape(phash or "-")}</div>'
            ),
            f"{sha} {phash}",
        )

    def get_column_updated_at_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染 LogoAsset 更新时间。"""
        value, display = format_temporal_value(row.updated_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: CatalogLogoAsset, **kwargs: object):
        """渲染远程图片对比 om-modal 动作。"""
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#logo-asset-compare-modal" data-om-modal-url="/logo-assets/{int(row.id)}/compare-modal"><i class="ri-image-line align-bottom mr-2 text-default-500"></i>{escape(_("Compare Images"))}</button></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )


class MatchDecisionTable(SQLAlchemyTableView):
    """人工匹配决策审计表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "match_decisions_table"
    route_path = "/match-decisions/table"
    model = CatalogMatchDecision
    page_size = 10
    selectable = True
    ordering = ["-created_at"]
    search_fields = ["decision_scope", "reason", "decided_by", "catalog_feed.tvg_id"]
    loader_options = [
        selectinload(CatalogMatchDecision.source_record),
        selectinload(CatalogMatchDecision.catalog_channel),
        selectinload(CatalogMatchDecision.catalog_feed),
    ]
    unsortable_columns = ["source_record.source_record_key", "catalog_channel.identity_name", "catalog_feed.tvg_id", "evidence_json", "action"]
    empty_message = _("No match decisions found.")
    columns = [
        (_("Scope"), "decision_scope", "get_column_decision_scope_data"),
        (_("Source"), "source_record.source_record_key", "get_column_source_record_data"),
        (_("Channel"), "catalog_channel.identity_name", "get_column_catalog_channel_data"),
        (_("Feed"), "catalog_feed.tvg_id", "get_column_catalog_feed_data"),
        (_("Decision"), "decision", "get_column_decision_data"),
        (_("Reason"), "reason", "get_column_reason_data"),
        (_("Evidence"), "evidence_json", "get_column_evidence_json_data"),
        (_("Decided By"), "decided_by"),
        (_("Created"), "created_at", "get_column_created_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问人工匹配决策表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回人工匹配决策基础查询。"""
        return select(CatalogMatchDecision)

    async def apply_search(self, query, table_request):
        """搜索决策和 feed 字段，关联为空时仍保留基础记录。"""
        search = table_request.q.strip()
        if not search:
            return query
        like = f"%{search}%"
        return (
            query.outerjoin(CatalogMatchDecision.catalog_feed)
            .where(
                or_(
                    CatalogMatchDecision.decision_scope.like(like),
                    CatalogMatchDecision.reason.like(like),
                    CatalogMatchDecision.decided_by.like(like),
                    CatalogFeed.tvg_id.like(like),
                )
            )
        )

    async def filter_decision_scope(self, query, value: object, table_request):
        """按决策范围筛选。"""
        value_text = str(value).strip()
        return query.where(CatalogMatchDecision.decision_scope == value_text) if value_text else query

    async def filter_decision(self, query, value: object, table_request):
        """按人工决策状态筛选。"""
        value_text = str(value).strip()
        return query.where(CatalogMatchDecision.decision == value_text) if value_text else query

    async def filter_decided_by(self, query, value: object, table_request):
        """按操作者筛选人工决策。"""
        value_text = str(value).strip()
        return query.where(CatalogMatchDecision.decided_by.like(f"%{value_text}%")) if value_text else query

    async def filter_source_record_id(self, query, value: object, table_request):
        """按上游记录 ID 筛选。"""
        return query.where(CatalogMatchDecision.source_record_id == parse_relation_filter_id(value, label=str(_("Source Record"))))

    async def filter_catalog_channel_id(self, query, value: object, table_request):
        """按 CatalogChannel ID 筛选。"""
        return query.where(CatalogMatchDecision.catalog_channel_id == parse_relation_filter_id(value, label=str(_("Catalog Channel"))))

    async def filter_catalog_feed_id(self, query, value: object, table_request):
        """按 CatalogFeed ID 筛选。"""
        return query.where(CatalogMatchDecision.catalog_feed_id == parse_relation_filter_id(value, label=str(_("Catalog Feed"))))

    def get_column_decision_scope_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染决策范围 badge。"""
        return badge(row.decision_scope or "unknown", tone="info"), row.decision_scope or ""

    def get_column_source_record_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染关联的上游记录摘要。"""
        record = getattr(row, "source_record", None)
        if record is None:
            return badge(str(_("Unbound")), tone="secondary"), ""
        label = f"{record.source_code}/{record.source_record_key}"
        primary = record.primary_name or ""
        return (
            Markup(
                f'<span class="font-medium text-body">{escape(label)}</span>'
                f'<div class="text-default-500 text-xs truncate" style="max-width: 220px;">{escape(primary or "-")}</div>'
            ),
            f"{label} {primary}",
        )

    def get_column_catalog_channel_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染关联 CatalogChannel。"""
        channel = getattr(row, "catalog_channel", None)
        if channel is None:
            return badge(str(_("Unbound")), tone="secondary"), ""
        name = channel.identity_name or channel.channel_id or str(channel.id)
        channel_id = channel.channel_id or channel.channel_key or str(channel.id)
        return (
            Markup(
                f'<a href="/catalog-channels/{channel.id}/edit" class="link-primary font-medium">{escape(name)}</a>'
                f'<div class="text-default-500 text-xs">{escape(channel_id)}</div>'
            ),
            f"{name} {channel_id}",
        )

    def get_column_catalog_feed_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染关联 CatalogFeed。"""
        feed = getattr(row, "catalog_feed", None)
        if feed is None:
            return badge(str(_("Unbound")), tone="secondary"), ""
        tvg_id = feed.tvg_id or str(feed.id)
        name = feed.canonical_name or tvg_id
        return (
            Markup(
                f'<a href="/catalog-feeds/{feed.id}/edit" class="link-primary font-medium">{escape(tvg_id)}</a>'
                f'<div class="text-default-500 text-xs">{escape(name)}</div>'
            ),
            f"{tvg_id} {name}",
        )

    def get_column_decision_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染人工决策状态 badge。"""
        tones = {
            "accepted": "success",
            "rejected": "danger",
            "manual_review": "warning",
            "ignored": "secondary",
            "merged": "primary",
        }
        decision = row.decision or "unknown"
        return badge(str(_(decision.replace("_", " ").title())), tone=tones.get(row.decision, "secondary")), row.decision or ""

    def get_column_reason_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染人工决策理由。"""
        return muted_truncate(row.reason, max_width=260), row.reason or ""

    def get_column_evidence_json_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染证据 JSON 摘要，避免长文本撑开审计表。"""
        return muted_truncate(row.evidence_json, max_width=280), row.evidence_json or ""

    def get_column_created_at_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染决策创建时间。"""
        value, display = format_temporal_value(row.created_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: CatalogMatchDecision, **kwargs: object):
        """渲染人工决策编辑弹窗动作。"""
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#match-decision-edit-modal" data-om-modal-url="/match-decisions/{int(row.id)}/edit-modal"><i class="ri-pencil-fill align-bottom mr-2 text-default-500"></i>{escape(_("Edit Decision"))}</button></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )


class NotificationTable(BaseTableView):
    """通知中心实时计算表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "notifications_table"
    route_path = "/notifications/table"
    page_size = 10
    selectable = True
    ordering = ["-created_at"]
    search_fields = ["title", "description", "source_label", "source_model"]
    unsortable_columns = ["action"]
    empty_message = _("No notifications found.")
    columns = [
        (_("Type"), "notification_type", "get_column_notification_type_data"),
        (_("Severity"), "severity", "get_column_severity_data"),
        (_("Title"), "title", "get_column_title_data"),
        (_("Source"), "source_label", "get_column_source_label_data"),
        (_("Created"), "created_at", "get_column_created_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问通知中心。"""
        return is_authenticated_request(request)

    async def get_object_list(self):
        """读取最近一批实时通知项，返回结构化数据源。"""
        return await services.notification_items(limit=services.NOTIFICATION_CENTER_LIMIT)

    async def filter_notification_type(self, rows, value: object, table_request):
        """按通知类型筛选实时通知。"""
        allowed = {"decision", "logo", "upstream"}
        normalized = str(value).strip()
        if normalized not in allowed:
            raise TableValidationError(_("Invalid notification type filter"))
        return [row for row in rows if row.notification_type == normalized]

    async def filter_severity(self, rows, value: object, table_request):
        """按严重程度筛选实时通知。"""
        allowed = {"critical", "warning", "info"}
        normalized = str(value).strip()
        if normalized not in allowed:
            raise TableValidationError(_("Invalid notification severity filter"))
        return [row for row in rows if row.severity == normalized]

    async def filter_created_from(self, rows, value: object, table_request):
        """按通知时间下限筛选实时通知。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid notification date filter"))
        return [row for row in rows if row.created_at is not None and row.created_at >= parsed] if parsed else rows

    async def filter_created_to(self, rows, value: object, table_request):
        """按通知时间上限筛选实时通知。"""
        parsed = parse_filter_datetime(value)
        if parsed is None and value not in {"", None}:
            raise TableValidationError(_("Invalid notification date filter"))
        return [row for row in rows if row.created_at is not None and row.created_at <= parsed] if parsed else rows

    def get_column_notification_type_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知类型 badge。"""
        labels = {"decision": _("Decision"), "logo": _("Logo"), "upstream": _("Upstream")}
        tones = {"decision": "danger", "logo": "warning", "upstream": "info"}
        return badge(str(labels.get(row.notification_type, row.notification_type)), tone=tones.get(row.notification_type, "secondary")), row.notification_type

    def get_column_severity_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知严重程度 badge。"""
        tones = {"critical": "danger", "warning": "warning", "info": "info"}
        return badge(str(_(row.severity.title())), tone=tones.get(row.severity, "secondary")), row.severity

    def get_column_title_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知标题和描述。"""
        return (
            Markup(
                f'<a href="{escape(row.href)}" class="link-primary font-medium">{escape(row.title)}</a>'
                f'<div class="text-default-500 text-xs truncate" style="max-width: 360px;">{escape(row.description)}</div>'
            ),
            f"{row.title} {row.description}",
        )

    def get_column_source_label_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知关联对象来源。"""
        return (
            Markup(
                f'<span class="font-medium">{escape(row.source_label)}</span>'
                f'<div class="text-default-500 text-xs">{escape(row.source_model)}</div>'
            ),
            f"{row.source_label} {row.source_model}",
        )

    def get_column_created_at_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知产生时间。"""
        value, display = format_temporal_value(row.created_at, date_format="%d %b, %Y %H:%M")
        return Markup(f'<span class="text-default-500">{escape(display)}</span>'), value

    def get_column_action_data(self, row: services.NotificationItem, **kwargs: object):
        """渲染通知详情 om-modal 动作。"""
        return (
            Markup(
                '<div class="om-dropdown">'
                f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#notification-detail-modal" data-om-modal-url="/notifications/detail/{escape(row.id)}"><i class="ri-eye-line align-bottom mr-2 text-default-500"></i>{escape(_("View Detail"))}</button></li>'
                f'<li><a class="om-dropdown-item" href="{escape(row.href)}"><i class="ri-arrow-right-line align-bottom mr-2 text-default-500"></i>{escape(_("Open Source"))}</a></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )


def action_link(url: str, label: str, *, class_name: str = "") -> Markup:
    """渲染表格内动作链接。"""
    css_class = f"link-primary {class_name}".strip()
    return Markup(f'<a href="{escape(url)}" class="{escape(css_class)}">{escape(label)}</a>')


def format_temporal_value(value: object, *, date_format: str) -> tuple[str, str]:
    """把日期时间值转换为 raw/display，兼容测试夹具中的字符串。"""
    if value in {"", None}:
        return "", "-"
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(), value.strftime(date_format)
    text = str(value)
    return text, text


def badge(label: str, *, tone: str = "secondary") -> Markup:
    """渲染 Tailwind/Oldman badge，供业务列回调复用。"""
    allowed_tones = {"primary", "secondary", "success", "info", "warning", "danger"}
    safe_tone = tone if tone in allowed_tones else "secondary"
    tone_class = "default" if safe_tone == "secondary" else safe_tone
    return Markup(f'<span class="om-badge om-badge-{escape(tone_class)}">{escape(label)}</span>')


def first_logo_path(logo_asset: object | None) -> str:
    """按预览优先级读取 logo 资产路径。"""
    if logo_asset is None:
        return ""
    for attr in ("preview_path", "normalized_path", "original_path", "original_url", "source_url"):
        value = getattr(logo_asset, attr, None)
        if value:
            return str(value)
    return ""


def image_placeholder() -> tuple[Markup, str]:
    """返回 Logo 图片不可用时的稳定占位单元格。"""
    return (
        Markup(
            '<div class="om-avatar-sm"><span class="om-avatar-title rounded bg-default-50 text-default-500 border">'
            '<i class="ri-image-line"></i>'
            "</span></div>"
        ),
        "",
    )


def logo_alt_text(logo_asset: object) -> str:
    """根据绑定 feed 生成图片 alt 文案。"""
    feed = getattr(logo_asset, "catalog_feed", None)
    if feed is None:
        return "Logo preview"
    return str(getattr(feed, "canonical_name", None) or getattr(feed, "tvg_id", None) or "Logo preview")


def resolve_logo_src(path: str) -> str:
    """把存在的本地 logo 路径转换为浏览器可请求地址，缺失文件返回空字符串。"""
    if path.startswith("data:"):
        return path
    if path.startswith(("http://", "https://")):
        return ""

    candidate = Path(path)
    absolute = candidate if candidate.is_absolute() else Path.cwd() / path.lstrip("/")
    if not absolute.exists():
        return ""
    try:
        relative = absolute.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return ""
    return f"/media/{relative.as_posix()}"


def parse_score_filter(value: object, *, label: str) -> int:
    """解析 0-100 分值筛选，非法输入转为表格校验错误。"""
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError):
        raise TableValidationError(str(_("Invalid %(label)s filter", label=label))) from None
    if score < 0 or score > 100:
        raise TableValidationError(str(_("Invalid %(label)s filter", label=label)))
    return score


def action_dropdown(edit_url: str) -> Markup:
    """渲染表格 action 下拉菜单，只暴露当前真实可用动作。"""
    safe_url = escape(edit_url)
    return Markup(
        '<div class="om-dropdown">'
        f'<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false" aria-label="{escape(_("Row actions"))}">'
        '<i class="ri-more-fill align-middle"></i>'
        "</button>"
        '<ul class="om-dropdown-menu om-dropdown-menu-end">'
        f'<li><a class="om-dropdown-item" href="{safe_url}"><i class="ri-pencil-fill align-bottom mr-2 text-default-500"></i>{escape(_("Edit"))}</a></li>'
        "</ul>"
        "</div>"
    )
