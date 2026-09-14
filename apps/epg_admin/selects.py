"""EPG 后台 Select/Autocomplete provider。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from apps.epg_admin.models import CatalogChannel, CatalogFeed, ChannelName, ChannelsEpg, UpstreamSourceRecord
from apps.epg_admin.tables import is_authenticated_request
from oldman.web.components.selects import ModelSelectProvider, SelectChoice, select_registry


@select_registry.register("channels")
class ChannelSelectProvider(ModelSelectProvider):
    """频道远程选择器 provider。"""

    model = ChannelsEpg
    search_fields = ["name", "src_url", "country"]
    label_field = "name"
    value_field = "id"
    page_size = 20

    async def check_auth(self, request: Any, context) -> bool:
        """检查当前请求是否允许访问频道选择器。"""
        return is_authenticated_request(request)

    async def get_queryset(self, request: Any, context):
        """返回当前用户可搜索的频道查询。"""
        return select(ChannelsEpg).order_by(ChannelsEpg.name.asc())


@select_registry.register("channel_names")
class ChannelNameSelectProvider(ModelSelectProvider):
    """频道名称远程选择器 provider。"""

    model = ChannelName
    search_fields = ["name", "name_cn", "name_tw", "name_es", "logo"]
    label_field = "name"
    value_field = "id"
    page_size = 20

    async def check_auth(self, request: Any, context) -> bool:
        """检查当前请求是否允许访问频道名称选择器。"""
        return is_authenticated_request(request)

    async def get_queryset(self, request: Any, context):
        """返回当前用户可搜索的频道名称查询。"""
        return select(ChannelName).order_by(ChannelName.name.asc())


@select_registry.register("catalog_channels")
class CatalogChannelSelectProvider(ModelSelectProvider):
    """CatalogChannel 远程选择器 provider。"""

    model = CatalogChannel
    search_fields = ["channel_key", "channel_id", "identity_name", "owner_country_code"]
    label_field = "identity_name"
    value_field = "id"
    page_size = 20

    async def check_auth(self, request: Any, context) -> bool:
        """检查当前请求是否允许访问频道目录选择器。"""
        return is_authenticated_request(request)

    async def get_queryset(self, request: Any, context):
        """返回当前用户可搜索的频道目录查询。"""
        return select(CatalogChannel).order_by(CatalogChannel.identity_name.asc(), CatalogChannel.channel_id.asc())

    def get_option(self, obj: CatalogChannel):
        """把 CatalogChannel 转换为远程选择器显示项。"""
        identity = obj.identity_name or obj.channel_key or str(obj.id)
        channel_id = obj.channel_id or obj.channel_key or str(obj.id)
        return SelectChoice(id=obj.id, text=f"{identity} ({channel_id})")


@select_registry.register("catalog_feeds")
class CatalogFeedSelectProvider(ModelSelectProvider):
    """CatalogFeed 远程选择器 provider。"""

    model = CatalogFeed
    search_fields = ["tvg_id", "canonical_name", "accepted_names_text", "compatible_tvg_ids_text", "service_country_code"]
    label_field = "canonical_name"
    value_field = "id"
    page_size = 20

    async def check_auth(self, request: Any, context) -> bool:
        """检查当前请求是否允许访问 feed 选择器。"""
        return is_authenticated_request(request)

    async def get_queryset(self, request: Any, context):
        """返回当前用户可搜索的 feed 查询。"""
        return select(CatalogFeed).order_by(CatalogFeed.canonical_name.asc(), CatalogFeed.tvg_id.asc())

    def get_option(self, obj: CatalogFeed):
        """把 CatalogFeed 转换为远程选择器显示项。"""
        name = obj.canonical_name or obj.tvg_id or str(obj.id)
        tvg_id = obj.tvg_id or str(obj.id)
        country = f" · {obj.service_country_code}" if obj.service_country_code else ""
        return SelectChoice(id=obj.id, text=f"{name} ({tvg_id}){country}")


@select_registry.register("upstream_records")
class UpstreamRecordSelectProvider(ModelSelectProvider):
    """上游原始记录远程选择器 provider。"""

    model = UpstreamSourceRecord
    search_fields = ["source_code", "source_record_key", "primary_name", "raw_hash"]
    label_field = "source_record_key"
    value_field = "id"
    page_size = 20

    async def check_auth(self, request: Any, context) -> bool:
        """检查当前请求是否允许访问上游记录选择器。"""
        return is_authenticated_request(request)

    async def get_queryset(self, request: Any, context):
        """返回当前用户可搜索的上游记录查询。"""
        return select(UpstreamSourceRecord).order_by(UpstreamSourceRecord.last_seen_at.desc(), UpstreamSourceRecord.id.desc())

    def get_option(self, obj: UpstreamSourceRecord):
        """把 UpstreamSourceRecord 转换为远程选择器显示项。"""
        key = obj.source_record_key or str(obj.id)
        source = obj.source_code or "source"
        name = f" · {obj.primary_name}" if obj.primary_name else ""
        return SelectChoice(id=obj.id, text=f"{source}/{key}{name}")
