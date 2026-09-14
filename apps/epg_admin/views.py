"""ChannelsEpg、ChannelName 与 EpgList 后台 CRUD 视图。"""

from __future__ import annotations

from markupsafe import escape
from sqlalchemy import case, func, select

from apps.auth.decorators import admin_required
from apps.epg_admin import selects as _epg_selects  # noqa: F401
from apps.epg_admin import services
from apps.epg_admin.form_responses import form_error_response, form_success_response
from apps.epg_admin.forms import (
    CatalogChannelFilterForm,
    CatalogChannelForm,
    CatalogFeedFilterForm,
    CatalogFeedForm,
    ChannelNameFilterForm,
    ChannelNameForm,
    ChannelsEpgFilterForm,
    ChannelsEpgForm,
    EpgListFilterForm,
    EpgListForm,
    LogoAssetFilterForm,
    MatchDecisionFilterForm,
    MatchDecisionForm,
    NotificationFilterForm,
    UpstreamRecordFilterForm,
)
from apps.epg_admin.models import CatalogFeed, CatalogLogoAsset, CatalogMatchDecision
from apps.epg_admin.tables import (
    CatalogChannelTable,
    CatalogFeedTable,
    ChannelNameTable,
    ChannelsEpgTable,
    EpgListTable,
    LogoAssetTable,
    MatchDecisionTable,
    NotificationTable,
    UpstreamRecordTable,
    is_authenticated_request,
    logo_alt_text,
    resolve_logo_src,
)
from oldman.db import db_manager
from oldman.i18n import gettext as _
from oldman.web.api import (
    ApiErrorCode,
    CloseModalAction,
    DefaultApiFormResponse,
    FeedbackAction,
    ReloadTableAction,
)
from oldman.web.components.charts import (
    ChartResult,
    ChartSeries,
    ChartSummary,
    SQLAlchemyChartView,
    TailwindChartRenderer,
)
from oldman.web.components.selects import SelectProviderView
from oldman.web.request import Request
from oldman.web.response import json_response, redirect_response
from oldman.web.routing import get_app
from oldman.web.security import WebSecurityPurpose, configured_web_security_key
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template as render

SELECT_BINDING_SECRET = configured_web_security_key(
    WebSecurityPurpose.SELECT_BINDING
)


class LogoAssetQualityDistributionChart(SQLAlchemyChartView):
    """Logo 工作台质量分布图表 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "logo_asset_quality_distribution_chart"
    route_path = "/logo-assets/charts/quality-distribution"
    chart_type = "bar"
    default_range = "all"
    default_metric = "logo_quality"
    allowed_ranges = ("all",)
    allowed_metrics = ("logo_quality",)
    allowed_chart_types = ("bar",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取 Logo 图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """按质量分桶聚合 Logo 资产。"""
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
        rows = (
            await self.require_db_session().execute(
                select(bucket_expr.label("bucket"), func.count(CatalogLogoAsset.id).label("total"), func.min(order_expr).label("sort_order"))
                .group_by(bucket_expr)
                .order_by("sort_order")
            )
        ).all()
        bucket_labels = {"Low": _("Low"), "Medium": _("Medium"), "High": _("High")}
        data = [{"x": str(bucket_labels.get(str(row.bucket), row.bucket)), "y": int(row.total or 0)} for row in rows]
        total = sum(item["y"] for item in data)
        return ChartResult(
            series=[ChartSeries(name=str(_("Logo Assets")), data=data)] if data else [],
            labels=[item["x"] for item in data],
            summary=[ChartSummary(label=str(_("Total Logos")), value=total, tone="warning")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "bar", "height": 280, "toolbar": {"show": False}},
        )


class LogoAssetMimeDistributionChart(SQLAlchemyChartView):
    """Logo 工作台 MIME 类型分布图表 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "logo_asset_mime_distribution_chart"
    route_path = "/logo-assets/charts/mime-distribution"
    chart_type = "bar"
    default_range = "all"
    default_metric = "logo_mime"
    allowed_ranges = ("all",)
    allowed_metrics = ("logo_mime",)
    allowed_chart_types = ("bar",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取 Logo 图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """按 MIME 类型聚合 Logo 资产。"""
        mime_expr = func.coalesce(CatalogLogoAsset.mime_type, "unknown")
        rows = (
            await self.require_db_session().execute(
                select(mime_expr.label("mime_type"), func.count(CatalogLogoAsset.id).label("total"))
                .group_by(mime_expr)
                .order_by(func.count(CatalogLogoAsset.id).desc(), mime_expr.asc())
            )
        ).all()
        data = [{"x": str(row.mime_type or "unknown"), "y": int(row.total or 0)} for row in rows]
        total = sum(item["y"] for item in data)
        return ChartResult(
            series=[ChartSeries(name=str(_("Logo Assets")), data=data)] if data else [],
            labels=[item["x"] for item in data],
            summary=[ChartSummary(label=str(_("Total MIME Groups")), value=len(data), tone="info"), ChartSummary(label=str(_("Total Logos")), value=total, tone="primary")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "bar", "height": 280, "toolbar": {"show": False}},
        )


class LogoAssetDimensionScatterChart(SQLAlchemyChartView):
    """Logo 工作台尺寸散点图 data endpoint。"""

    renderer_class = TailwindChartRenderer
    route_name = "logo_asset_dimension_scatter_chart"
    route_path = "/logo-assets/charts/dimensions"
    chart_type = "scatter"
    default_range = "all"
    default_metric = "logo_dimensions"
    allowed_ranges = ("all",)
    allowed_metrics = ("logo_dimensions",)
    allowed_chart_types = ("scatter",)

    async def check_auth(self, request: Request) -> bool:
        """检查当前请求是否允许读取 Logo 图表。"""
        return is_authenticated_request(request)

    async def get_result(self, chart_request):
        """读取 Logo 宽高和质量分，返回尺寸散点图配置。"""
        rows = (
            await self.require_db_session().execute(
                select(CatalogLogoAsset.width, CatalogLogoAsset.height, CatalogLogoAsset.quality_score, CatalogFeed.tvg_id)
                .join(CatalogLogoAsset.catalog_feed)
                .order_by(CatalogLogoAsset.updated_at.desc())
                .limit(250)
            )
        ).all()
        data = [
            {
                "x": int(row.width or 0),
                "y": int(row.height or 0),
                "quality": int(row.quality_score or 0),
                "feed": str(row.tvg_id or ""),
            }
            for row in rows
        ]
        return ChartResult(
            series=[ChartSeries(name=str(_("Dimensions")), data=data)] if data else [],
            labels=[],
            summary=[ChartSummary(label=str(_("Sampled Logos")), value=len(data), tone="success")],
            meta={"range": chart_request.range_key, "metric": chart_request.metric},
            chart={"type": "scatter", "height": 280, "toolbar": {"show": False}, "zoom": {"enabled": False}},
        )


app = get_app()
app.add_route(ChannelsEpgTable.as_view(), ChannelsEpgTable.route_path, name=ChannelsEpgTable.route_name)
app.add_route(EpgListTable.as_view(), EpgListTable.route_path, name=EpgListTable.route_name)
app.add_route(ChannelNameTable.as_view(), ChannelNameTable.route_path, name=ChannelNameTable.route_name)
app.add_route(CatalogChannelTable.as_view(), CatalogChannelTable.route_path, name=CatalogChannelTable.route_name)
app.add_route(CatalogFeedTable.as_view(), CatalogFeedTable.route_path, name=CatalogFeedTable.route_name)
app.add_route(UpstreamRecordTable.as_view(), UpstreamRecordTable.route_path, name=UpstreamRecordTable.route_name)
app.add_route(LogoAssetTable.as_view(), LogoAssetTable.route_path, name=LogoAssetTable.route_name)
app.add_route(MatchDecisionTable.as_view(), MatchDecisionTable.route_path, name=MatchDecisionTable.route_name)
app.add_route(NotificationTable.as_view(), NotificationTable.route_path, name=NotificationTable.route_name)
app.add_route(LogoAssetQualityDistributionChart.as_view(), LogoAssetQualityDistributionChart.route_path, name=LogoAssetQualityDistributionChart.route_name)
app.add_route(LogoAssetMimeDistributionChart.as_view(), LogoAssetMimeDistributionChart.route_path, name=LogoAssetMimeDistributionChart.route_name)
app.add_route(LogoAssetDimensionScatterChart.as_view(), LogoAssetDimensionScatterChart.route_path, name=LogoAssetDimensionScatterChart.route_name)
app.add_route(SelectProviderView.as_view(secret_key=SELECT_BINDING_SECRET), "/admin/select/<provider_name:str>", name="admin_select_provider")


async def save_model_form(form) -> object:
    """在项目写 session 内保存 ModelForm 实例。"""
    async with db_manager.get_session() as session:
        return await form.save(commit=True, session=session)


def render_logo_compare_card(asset: CatalogLogoAsset, attr_name: str, label: str) -> str:
    """渲染 Logo 对比 om-modal 中的单张图片卡片，路径缺失时显示安全 fallback。"""
    path = getattr(asset, attr_name, None)
    alt = f"{logo_alt_text(asset)} {label}"
    src = resolve_logo_src(str(path)) if path else ""
    if src:
        media = (
            f'<img src="{escape(src)}" alt="{escape(alt)}" '
            'class="block w-full rounded border border-default-200 bg-default-50 object-contain" '
            'style="max-height: 180px;" loading="lazy">'
        )
    else:
        media = (
            '<div class="flex items-center justify-center rounded border border-default-200 bg-default-50 text-default-500" style="height: 180px;">'
            f'<span class="text-xs">{escape(_("Image unavailable"))}</span>'
            "</div>"
        )
    return (
        '<div class="md:col-span-6">'
        '<div class="h-full rounded border border-default-200 p-2">'
        f'<div class="text-default-500 text-xs mb-2">{escape(label)}</div>'
        f"{media}"
        f'<div class="text-default-500 text-xs truncate mt-2">{escape(str(path or "-"))}</div>'
        "</div>"
        "</div>"
    )


@app.get("/channels-epg", name="channels_epg")
@admin_required()
async def channels_epg_index(request: Request):
    """渲染 ChannelsEpg 列表。"""
    table = ChannelsEpgTable(request=request, initial_query=request.args.get("q", "").strip())
    return await render(
        "pages/channels_epg/index.html",
        context={
            "active_section": "epg",
            "active_page": "channels_epg",
            "filter_form": ChannelsEpgFilterForm.from_query(request),
            "table": table,
        },
    )


@app.get("/channels-epg/new", name="channels_epg_new")
@add_csrf_token()
@admin_required()
async def channels_epg_new(request: Request):
    """渲染 ChannelsEpg 新建表单。"""
    form = ChannelsEpgForm(request=request)
    return await render("pages/channels_epg/form.html", context={"active_section": "epg", "active_page": "channels_epg", "channel": None, "form": form})


@app.post("/channels-epg/new", name="channels_epg_create")
@csrf_protect()
@admin_required()
async def channels_epg_create(request: Request):
    """创建 ChannelsEpg。"""
    form = ChannelsEpgForm.from_request(request)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/channels_epg/form.html",
            context={"active_section": "epg", "active_page": "channels_epg", "channel": None, "form": form},
            cancel_url="/channels-epg",
        )
    await save_model_form(form)
    return form_success_response(request, "/channels-epg")


@app.get("/channels-epg/<channel_id:int>/edit", name="channels_epg_edit")
@add_csrf_token()
@admin_required()
async def channels_epg_edit(request: Request, channel_id: int):
    """渲染 ChannelsEpg 编辑表单。"""
    channel = await services.get_channel(channel_id)
    form = ChannelsEpgForm(request=request, instance=channel)
    return await render("pages/channels_epg/form.html", context={"active_section": "epg", "active_page": "channels_epg", "channel": channel, "form": form})


@app.post("/channels-epg/<channel_id:int>/edit", name="channels_epg_update")
@csrf_protect()
@admin_required()
async def channels_epg_update(request: Request, channel_id: int):
    """更新 ChannelsEpg。"""
    channel = await services.get_channel(channel_id)
    form = ChannelsEpgForm.from_request(request, instance=channel)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/channels_epg/form.html",
            context={"active_section": "epg", "active_page": "channels_epg", "channel": channel, "form": form},
            cancel_url="/channels-epg",
        )
    await save_model_form(form)
    return form_success_response(request, "/channels-epg")


@app.post("/channels-epg/<channel_id:int>/delete", name="channels_epg_delete")
@csrf_protect()
@admin_required()
async def channels_epg_delete(request: Request, channel_id: int):
    """删除 ChannelsEpg。"""
    await services.delete_channel(channel_id)
    return redirect_response("/channels-epg", status=303)


@app.get("/channel-names", name="channel_names")
@admin_required()
async def channel_names_index(request: Request):
    """渲染 ChannelName 列表。"""
    published = request.args.get("published", "").strip()
    country_id = request.args.get("country_id", "").strip()
    language_id = request.args.get("language_id", "").strip()
    bound_epg = request.args.get("bound_epg", "").strip()
    table = ChannelNameTable(
        request=request,
        initial_filters={
            "published": published,
            "country_id": country_id,
            "language_id": language_id,
            "bound_epg": bound_epg,
        },
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/channel_names/index.html",
        context={
            "active_section": "epg",
            "active_page": "channel_names",
            "filter_form": ChannelNameFilterForm.from_query(request),
            "table": table,
        },
    )


@app.get("/channel-names/new", name="channel_names_new")
@add_csrf_token()
@admin_required()
async def channel_names_new(request: Request):
    """渲染 ChannelName 新建表单。"""
    reference_ids = await services.channel_name_reference_defaults()
    form = ChannelNameForm(request=request, select_secret_key=SELECT_BINDING_SECRET, reference_ids=reference_ids)
    return await render(
        "pages/channel_names/form.html",
        context={"active_section": "epg", "active_page": "channel_names", "channel_name": None, "form": form},
    )


@app.post("/channel-names/new", name="channel_names_create")
@csrf_protect()
@admin_required()
async def channel_names_create(request: Request):
    """创建 ChannelName。"""
    reference_ids = await services.channel_name_reference_defaults()
    form = ChannelNameForm.from_request(request, select_secret_key=SELECT_BINDING_SECRET, reference_ids=reference_ids)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/channel_names/form.html",
            context={"active_section": "epg", "active_page": "channel_names", "channel_name": None, "form": form},
            cancel_url="/channel-names",
        )
    await save_model_form(form)
    return form_success_response(request, "/channel-names")


@app.get("/channel-names/<channel_name_id:int>/edit", name="channel_names_edit")
@add_csrf_token()
@admin_required()
async def channel_names_edit(request: Request, channel_name_id: int):
    """渲染 ChannelName 编辑表单。"""
    channel_name = await services.get_channel_name(channel_name_id)
    form = ChannelNameForm(request=request, instance=channel_name, select_secret_key=SELECT_BINDING_SECRET)
    return await render(
        "pages/channel_names/form.html",
        context={"active_section": "epg", "active_page": "channel_names", "channel_name": channel_name, "form": form},
    )


@app.post("/channel-names/<channel_name_id:int>/edit", name="channel_names_update")
@csrf_protect()
@admin_required()
async def channel_names_update(request: Request, channel_name_id: int):
    """更新 ChannelName。"""
    channel_name = await services.get_channel_name(channel_name_id)
    reference_ids = await services.channel_name_reference_defaults()
    form = ChannelNameForm.from_request(request, instance=channel_name, select_secret_key=SELECT_BINDING_SECRET, reference_ids=reference_ids)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/channel_names/form.html",
            context={"active_section": "epg", "active_page": "channel_names", "channel_name": channel_name, "form": form},
            cancel_url="/channel-names",
        )
    await save_model_form(form)
    return form_success_response(request, "/channel-names")


@app.post("/channel-names/<channel_name_id:int>/delete", name="channel_names_delete")
@csrf_protect()
@admin_required()
async def channel_names_delete(request: Request, channel_name_id: int):
    """删除 ChannelName。"""
    await services.delete_channel_name(channel_name_id)
    return redirect_response("/channel-names", status=303)


@app.get("/catalog-channels", name="catalog_channels")
@admin_required()
async def catalog_channels_index(request: Request):
    """渲染 CatalogChannel 列表。"""
    status = request.args.get("status", "").strip()
    owner_country_code = request.args.get("owner_country_code", "").strip()
    confidence_min = request.args.get("confidence_min", "").strip()
    confidence_max = request.args.get("confidence_max", "").strip()
    table = CatalogChannelTable(
        request=request,
        initial_filters={
            "status": status,
            "owner_country_code": owner_country_code,
            "confidence_min": confidence_min,
            "confidence_max": confidence_max,
        },
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/catalog_channels/index.html",
        context={
            "active_section": "catalog",
            "active_page": "catalog_channels",
            "filter_form": CatalogChannelFilterForm.from_query(request),
            "table": table,
        },
    )


@app.get("/catalog-channels/new", name="catalog_channels_new")
@add_csrf_token()
@admin_required()
async def catalog_channels_new(request: Request):
    """渲染 CatalogChannel 新建表单。"""
    form = CatalogChannelForm(request=request)
    return await render(
        "pages/catalog_channels/form.html",
        context={
            "active_section": "catalog",
            "active_page": "catalog_channels",
            "catalog_channel": None,
            "form": form,
        },
    )


@app.post("/catalog-channels/new", name="catalog_channels_create")
@csrf_protect()
@admin_required()
async def catalog_channels_create(request: Request):
    """创建 CatalogChannel。"""
    form = CatalogChannelForm.from_request(request)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/catalog_channels/form.html",
            context={
                "active_section": "catalog",
                "active_page": "catalog_channels",
                "catalog_channel": None,
                "form": form,
            },
            cancel_url="/catalog-channels",
        )
    await save_model_form(form)
    return form_success_response(request, "/catalog-channels")


@app.get("/catalog-channels/<catalog_channel_id:int>/edit", name="catalog_channels_edit")
@add_csrf_token()
@admin_required()
async def catalog_channels_edit(request: Request, catalog_channel_id: int):
    """渲染 CatalogChannel 编辑表单。"""
    catalog_channel = await services.get_catalog_channel(catalog_channel_id)
    form = CatalogChannelForm(request=request, instance=catalog_channel)
    return await render(
        "pages/catalog_channels/form.html",
        context={
            "active_section": "catalog",
            "active_page": "catalog_channels",
            "catalog_channel": catalog_channel,
            "form": form,
        },
    )


@app.post("/catalog-channels/<catalog_channel_id:int>/edit", name="catalog_channels_update")
@csrf_protect()
@admin_required()
async def catalog_channels_update(request: Request, catalog_channel_id: int):
    """更新 CatalogChannel。"""
    catalog_channel = await services.get_catalog_channel(catalog_channel_id)
    form = CatalogChannelForm.from_request(request, instance=catalog_channel)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/catalog_channels/form.html",
            context={
                "active_section": "catalog",
                "active_page": "catalog_channels",
                "catalog_channel": catalog_channel,
                "form": form,
            },
            cancel_url="/catalog-channels",
        )
    await save_model_form(form)
    return form_success_response(request, "/catalog-channels")


@app.post("/catalog-channels/<catalog_channel_id:int>/delete", name="catalog_channels_delete")
@csrf_protect()
@admin_required()
async def catalog_channels_delete(request: Request, catalog_channel_id: int):
    """删除 CatalogChannel。"""
    await services.delete_catalog_channel(catalog_channel_id)
    return redirect_response("/catalog-channels", status=303)


@app.get("/catalog-feeds", name="catalog_feeds")
@admin_required()
async def catalog_feeds_index(request: Request):
    """渲染 CatalogFeed 列表。"""
    filter_names = (
        "status",
        "service_country_code",
        "version_kind",
        "is_default",
        "has_logo",
        "created_from",
        "created_to",
        "updated_from",
        "updated_to",
    )
    table = CatalogFeedTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/catalog_feeds/index.html",
        context={
            "active_section": "catalog",
            "active_page": "catalog_feeds",
            "filter_form": CatalogFeedFilterForm.from_query(request),
            "table": table,
        },
    )


@app.get("/catalog-feeds/new", name="catalog_feeds_new")
@add_csrf_token()
@admin_required()
async def catalog_feeds_new(request: Request):
    """渲染 CatalogFeed 新建表单。"""
    form = CatalogFeedForm(request=request, select_secret_key=SELECT_BINDING_SECRET)
    return await render(
        "pages/catalog_feeds/form.html",
        context={"active_section": "catalog", "active_page": "catalog_feeds", "catalog_feed": None, "form": form},
    )


@app.post("/catalog-feeds/new", name="catalog_feeds_create")
@csrf_protect()
@admin_required()
async def catalog_feeds_create(request: Request):
    """创建 CatalogFeed。"""
    form = CatalogFeedForm.from_request(request, select_secret_key=SELECT_BINDING_SECRET)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/catalog_feeds/form.html",
            context={"active_section": "catalog", "active_page": "catalog_feeds", "catalog_feed": None, "form": form},
            cancel_url="/catalog-feeds",
        )
    await save_model_form(form)
    return form_success_response(request, "/catalog-feeds")


@app.get("/catalog-feeds/<catalog_feed_id:int>/edit", name="catalog_feeds_edit")
@add_csrf_token()
@admin_required()
async def catalog_feeds_edit(request: Request, catalog_feed_id: int):
    """渲染 CatalogFeed 编辑表单。"""
    catalog_feed = await services.get_catalog_feed(catalog_feed_id)
    form = CatalogFeedForm(request=request, instance=catalog_feed, select_secret_key=SELECT_BINDING_SECRET)
    return await render(
        "pages/catalog_feeds/form.html",
        context={"active_section": "catalog", "active_page": "catalog_feeds", "catalog_feed": catalog_feed, "form": form},
    )


@app.post("/catalog-feeds/<catalog_feed_id:int>/edit", name="catalog_feeds_update")
@csrf_protect()
@admin_required()
async def catalog_feeds_update(request: Request, catalog_feed_id: int):
    """更新 CatalogFeed。"""
    catalog_feed = await services.get_catalog_feed(catalog_feed_id)
    form = CatalogFeedForm.from_request(request, instance=catalog_feed, select_secret_key=SELECT_BINDING_SECRET)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/catalog_feeds/form.html",
            context={"active_section": "catalog", "active_page": "catalog_feeds", "catalog_feed": catalog_feed, "form": form},
            cancel_url="/catalog-feeds",
        )
    await save_model_form(form)
    return form_success_response(request, "/catalog-feeds")


@app.post("/catalog-feeds/<catalog_feed_id:int>/delete", name="catalog_feeds_delete")
@csrf_protect()
@admin_required()
async def catalog_feeds_delete(request: Request, catalog_feed_id: int):
    """删除 CatalogFeed。"""
    await services.delete_catalog_feed(catalog_feed_id)
    return redirect_response("/catalog-feeds", status=303)


@app.get("/upstream-records", name="upstream_records")
@admin_required()
async def upstream_records_index(request: Request):
    """渲染上游原始记录审计列表。"""
    filter_names = (
        "source_code",
        "status",
        "record_kind",
        "catalog_feed_id",
        "last_seen_from",
        "last_seen_to",
    )
    table = UpstreamRecordTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    facets = await services.upstream_record_facets()
    return await render(
        "pages/upstream_records/index.html",
        context={
            "active_section": "ingestion",
            "active_page": "upstream_records",
            "filter_form": UpstreamRecordFilterForm.from_query(request, select_secret_key=SELECT_BINDING_SECRET),
            "table": table,
            "facets": facets,
        },
    )


@app.get("/upstream-records/<record_id:int>/raw-modal", name="upstream_records_raw_modal")
@admin_required()
async def upstream_record_raw_modal(request: Request, record_id: int):
    """按需返回上游记录 raw payload 的 om-modal 片段，避免列表响应携带大字段。"""
    record = await services.get_upstream_record(record_id)
    if record is None:
        return json_response(
            {
                "title": str(_("Raw Payload")),
                "body": f'<p class="text-default-500 mb-0">{escape(_("Record not found."))}</p>',
                "footer": f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>',
            },
            status=404,
        )

    key = escape(record.source_record_key or str(record.id))
    payload = escape(record.raw_payload or "")
    return json_response(
        {
            "title": f'{_("Raw Payload")} · {key}',
            "body": f'<pre class="mb-0 text-xs text-default-500 break-words" style="white-space: pre-wrap;">{payload}</pre>',
            "footer": f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>',
        }
    )


@app.get("/logo-assets", name="logo_assets")
@admin_required()
async def logo_assets_index(request: Request):
    """渲染 Logo 资产质量工作台。"""
    filter_names = (
        "catalog_feed_id",
        "source_kind",
        "mime_type",
        "quality_min",
        "quality_max",
    )
    table = LogoAssetTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/logo_assets/index.html",
        context={
            "active_section": "ingestion",
            "active_page": "logo_assets",
            "filter_form": LogoAssetFilterForm.from_query(request, select_secret_key=SELECT_BINDING_SECRET),
            "table": table,
            "quality_distribution_chart": LogoAssetQualityDistributionChart(request=request),
            "mime_distribution_chart": LogoAssetMimeDistributionChart(request=request),
            "dimension_scatter_chart": LogoAssetDimensionScatterChart(request=request),
        },
    )


@app.get("/logo-assets/<logo_asset_id:int>/compare-modal", name="logo_assets_compare_modal")
@admin_required()
async def logo_asset_compare_modal(request: Request, logo_asset_id: int):
    """按需返回 Logo 原图、归一化图和特征图对比 om-modal 片段。"""
    asset = await services.get_logo_asset(logo_asset_id)
    if asset is None:
        return json_response(
            {
                "title": str(_("Logo Comparison")),
                "body": f'<p class="text-default-500 mb-0">{escape(_("Logo asset not found."))}</p>',
                "footer": f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>',
            },
            status=404,
        )

    feed = asset.catalog_feed
    title = escape(getattr(feed, "canonical_name", None) or getattr(feed, "tvg_id", None) or f"Logo #{asset.id}")
    cards = "".join(
        render_logo_compare_card(asset, attr_name, label)
        for attr_name, label in (
            ("original_path", str(_("Original"))),
            ("normalized_path", str(_("Normalized"))),
            ("preview_path", str(_("Preview"))),
            ("edge_map_path", str(_("Edge"))),
            ("mask_map_path", str(_("Mask"))),
        )
    )
    body = (
        '<div class="om-form-grid gap-3">'
        f"{cards}"
        "</div>"
        '<div class="mt-3 text-xs text-default-500">'
        f"sha256: {escape(asset.sha256 or '-')}"
        f" · phash: {escape(asset.phash or '-')}"
        f" · quality: {escape(str(asset.quality_score))}"
        "</div>"
    )
    return json_response(
        {
            "title": f'{_("Logo Comparison")} · {title}',
            "body": body,
            "footer": f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>',
        }
    )


@app.get("/match-decisions", name="match_decisions")
@admin_required()
async def match_decisions_index(request: Request):
    """渲染人工匹配决策审计列表。"""
    filter_names = (
        "decision_scope",
        "decision",
        "decided_by",
        "source_record_id",
        "catalog_channel_id",
        "catalog_feed_id",
    )
    table = MatchDecisionTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/match_decisions/index.html",
        context={
            "active_section": "catalog",
            "active_page": "match_decisions",
            "filter_form": MatchDecisionFilterForm.from_query(request, select_secret_key=SELECT_BINDING_SECRET),
            "table": table,
        },
    )


@app.get("/match-decisions/<decision_id:int>/edit-modal", name="match_decisions_edit_modal")
@add_csrf_token()
@admin_required()
async def match_decisions_edit_modal(request: Request, decision_id: int):
    """返回人工匹配决策编辑弹窗的远程 JSON 片段。"""
    decision = await services.get_match_decision(decision_id)
    if decision is None:
        return json_response(
            {"title": str(_("Edit Decision")), "html": f'<p class="text-default-500 mb-0">{escape(_("Decision not found."))}</p>'},
            status=404,
        )

    form = MatchDecisionForm(request=request, instance=decision, csrf_token=request.ctx.csrf_token)
    template = request.app.ext.environment.get_template("partials/match_decisions/edit_form.html")
    html = await template.render_async(decision=decision, form=form)
    return json_response({"title": f'{_("Edit Decision")} · {escape(match_decision_label(decision))}', "html": html})


@app.post("/match-decisions/<decision_id:int>/edit", name="match_decisions_update")
@csrf_protect()
@admin_required()
async def match_decisions_update(request: Request, decision_id: int):
    """处理人工匹配决策弹窗编辑提交。"""
    async with db_manager.get_session() as session:
        decision = await session.get(CatalogMatchDecision, decision_id)
        if decision is None:
            payload = DefaultApiFormResponse(
                error_code=ApiErrorCode.INVALID_REQUEST,
                message=str(_("Decision not found")),
            )
            return json_response(payload.to_dict(), status=404)

        form = MatchDecisionForm.from_request(request, instance=decision, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)

        await form.save(commit=True, session=session)

    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=str(_("Saved")),
        actions=[
            FeedbackAction(target="#match-decisions-feedback", title=str(_("Decision saved")), icon="success"),
            CloseModalAction(),
            ReloadTableAction(target="#match-decisions-table"),
        ],
    )
    return json_response(payload.to_dict())


def match_decision_label(decision: CatalogMatchDecision) -> str:
    """返回人工匹配决策弹窗标题使用的可读标识。"""
    feed = getattr(decision, "catalog_feed", None)
    channel = getattr(decision, "catalog_channel", None)
    record = getattr(decision, "source_record", None)
    if feed is not None:
        return str(feed.tvg_id or feed.id)
    if channel is not None:
        return str(channel.identity_name or channel.channel_id or channel.id)
    if record is not None:
        return str(record.source_record_key or record.id)
    return f"#{decision.id}"


@app.get("/notifications", name="notifications")
@admin_required()
async def notifications_index(request: Request):
    """渲染通知中心列表页。"""
    filter_names = ("notification_type", "severity", "created_from", "created_to")
    table = NotificationTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/notifications/index.html",
        context={
            "active_section": "system",
            "active_page": "notifications",
            "dashboard_notifications": await services.dashboard_notifications(),
            "filter_form": NotificationFilterForm.from_query(request),
            "notification_limit": services.NOTIFICATION_CENTER_LIMIT,
            "notification_stats": await services.notification_stats(),
            "table": table,
        },
    )


@app.get("/notifications/detail/<notification_id:path>", name="notification_detail_modal")
@admin_required()
async def notification_detail_modal(request: Request, notification_id: str):
    """返回通知关联对象详情弹窗片段。"""
    item = await find_notification_item(notification_id)
    if item is None:
        return json_response(
            {
                "title": str(_("Notification Detail")),
                "body": f'<p class="text-default-500 mb-0">{escape(_("Notification is no longer available."))}</p>',
                "footer": f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>',
            },
        )
    tone_class_map = {
        "primary": "bg-primary-50 text-primary-700",
        "success": "bg-green-50 text-green-700",
        "info": "bg-cyan-50 text-cyan-700",
        "warning": "bg-amber-50 text-amber-700",
        "danger": "bg-red-50 text-red-700",
    }
    tone_classes = tone_class_map.get(item.tone, "bg-default-100 text-default-600")
    body = (
        '<div class="flex items-start gap-3 w-full overflow-hidden">'
        '<div class="om-avatar-sm">'
        f'<span class="om-avatar-title {tone_classes} rounded-full text-lg">'
        f'<i class="{escape(item.icon)}"></i>'
        "</span>"
        "</div>"
        '<div class="flex-1 min-w-0">'
        f'<div class="om-badge om-badge-{escape(item.tone)} mb-2">{escape(_(item.severity.title()))}</div>'
        f"<p class=\"text-default-500 break-words mb-3\">{escape(item.description)}</p>"
        '<dl class="grid grid-cols-1 gap-2 sm:grid-cols-12 mb-0 text-xs">'
        f'<dt class="sm:col-span-4">{escape(_("Type"))}</dt><dd class="sm:col-span-8 break-words">{escape(item.notification_type)}</dd>'
        f'<dt class="sm:col-span-4">{escape(_("Source"))}</dt><dd class="sm:col-span-8 break-words">{escape(item.source_label)}</dd>'
        f'<dt class="sm:col-span-4">{escape(_("Model"))}</dt><dd class="sm:col-span-8 break-words">{escape(item.source_model)}</dd>'
        f'<dt class="sm:col-span-4">{escape(_("Created"))}</dt><dd class="sm:col-span-8 break-words">{escape(item.created_at or "-")}</dd>'
        "</dl>"
        "</div>"
        "</div>"
    )
    footer = (
        f'<button type="button" class="om-button om-button-light" data-om-modal-close>{escape(_("Close"))}</button>'
        f'<a class="om-button om-button-primary" href="{escape(item.href)}">{escape(_("Open Source"))}</a>'
    )
    return json_response({"title": escape(item.title), "body": body, "footer": footer})


async def find_notification_item(notification_id: str):
    """按稳定通知 ID 从实时通知列表中查找当前项。"""
    for item in await services.notification_items(limit=services.NOTIFICATION_CENTER_LIMIT):
        if item.id == notification_id:
            return item
    return None


@app.get("/epg-list", name="epg_list")
@admin_required()
async def epg_list_index(request: Request):
    """渲染 EpgList 列表。"""
    channel_id = request.args.get("channel_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    channel_choices = await services.channel_select_choices()
    table = EpgListTable(
        request=request,
        initial_filters={
            "channel_id": channel_id,
            "date_from": date_from,
            "date_to": date_to,
        },
        initial_query=request.args.get("q", "").strip(),
    )
    return await render(
        "pages/epg_list/index.html",
        context={
            "active_section": "epg",
            "active_page": "epg_list",
            "filter_form": EpgListFilterForm.from_query(request, channel_choices=channel_choices),
            "table": table,
        },
    )


@app.get("/epg-list/new", name="epg_list_new")
@add_csrf_token()
@admin_required()
async def epg_list_new(request: Request):
    """渲染 EpgList 新建表单。"""
    form = EpgListForm(request=request, select_secret_key=SELECT_BINDING_SECRET)
    return await render(
        "pages/epg_list/form.html",
        context={"active_section": "epg", "active_page": "epg_list", "item": None, "form": form},
    )


@app.post("/epg-list/new", name="epg_list_create")
@csrf_protect()
@admin_required()
async def epg_list_create(request: Request):
    """创建 EpgList。"""
    form = EpgListForm.from_request(request, select_secret_key=SELECT_BINDING_SECRET)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/epg_list/form.html",
            context={"active_section": "epg", "active_page": "epg_list", "item": None, "form": form},
            cancel_url="/epg-list",
        )
    await save_model_form(form)
    return form_success_response(request, "/epg-list")


@app.get("/epg-list/<item_id:int>/edit", name="epg_list_edit")
@add_csrf_token()
@admin_required()
async def epg_list_edit(request: Request, item_id: int):
    """渲染 EpgList 编辑表单。"""
    item = await services.get_epg_item(item_id)
    form = EpgListForm(request=request, instance=item, select_secret_key=SELECT_BINDING_SECRET)
    return await render(
        "pages/epg_list/form.html",
        context={"active_section": "epg", "active_page": "epg_list", "item": item, "form": form},
    )


@app.post("/epg-list/<item_id:int>/edit", name="epg_list_update")
@csrf_protect()
@admin_required()
async def epg_list_update(request: Request, item_id: int):
    """更新 EpgList。"""
    item = await services.get_epg_item(item_id)
    form = EpgListForm.from_request(request, instance=item, select_secret_key=SELECT_BINDING_SECRET)
    if not await form.validate():
        return await form_error_response(
            request,
            form,
            template="pages/epg_list/form.html",
            context={"active_section": "epg", "active_page": "epg_list", "item": item, "form": form},
            cancel_url="/epg-list",
        )
    await save_model_form(form)
    return form_success_response(request, "/epg-list")


@app.post("/epg-list/<item_id:int>/delete", name="epg_list_delete")
@csrf_protect()
@admin_required()
async def epg_list_delete(request: Request, item_id: int):
    """删除 EpgList。"""
    await services.delete_epg_item(item_id)
    return redirect_response("/epg-list", status=303)
