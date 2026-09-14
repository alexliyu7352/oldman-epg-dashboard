"""EPG 后台 CRUD 与统计服务。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

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
from oldman.db import db_manager
from oldman.i18n import gettext as _
from oldman.web.components.selects import SelectChoice

CHANNEL_NAME_REFERENCE_TABLES = {
    "category_id": "epg_channelcategory",
    "country_id": "epg_country",
    "language_id": "epg_language",
}

NOTIFICATION_CENTER_LIMIT = 500


@dataclass(frozen=True)
class NotificationItem:
    """通知中心的实时计算行，不对应持久化通知表。"""

    id: str
    notification_type: str
    severity: str
    title: str
    description: str
    created_at: dt.datetime | None
    href: str
    icon: str
    tone: str
    source_label: str
    source_model: str
    source_id: int | None

    @property
    def time(self) -> dt.datetime | None:
        """兼容 topbar 模板读取的时间字段。"""
        return self.created_at

    def as_topbar_dict(self) -> dict[str, Any]:
        """转换为现有 topbar 模板可消费的通知字典。"""
        return {
            "tone": self.tone,
            "icon": self.icon,
            "title": self.title,
            "description": self.description,
            "time": self.created_at,
            "href": self.href,
        }


async def get_channel(channel_id: int) -> ChannelsEpg | None:
    """读取单个 ChannelsEpg。"""
    async with db_manager.get_read_session() as session:
        return await session.get(ChannelsEpg, channel_id)


async def delete_channel(channel_id: int) -> None:
    """删除 ChannelsEpg。"""
    async with db_manager.get_session() as session:
        channel = await session.get(ChannelsEpg, channel_id)
        if channel is not None:
            await session.delete(channel)


async def get_epg_item(item_id: int) -> EpgList | None:
    """读取单个 EpgList，并预加载编辑表单需要回显的频道。"""
    async with db_manager.get_read_session() as session:
        result = await session.execute(select(EpgList).options(selectinload(EpgList.channel)).where(EpgList.id == item_id))
        return result.scalar_one_or_none()


async def delete_epg_item(item_id: int) -> None:
    """删除 EpgList。"""
    async with db_manager.get_session() as session:
        item = await session.get(EpgList, item_id)
        if item is not None:
            await session.delete(item)


async def get_channel_name(channel_name_id: int) -> ChannelName | None:
    """读取单个 ChannelName，并补齐编辑表单的频道绑定回显对象。"""
    async with db_manager.get_read_session() as session:
        channel_name = await session.get(ChannelName, channel_name_id)
        if channel_name is None or not channel_name.epg_id:
            return channel_name
        bound_epg = await session.get(ChannelsEpg, channel_name.epg_id)
        setattr(channel_name, "_bound_epg", bound_epg)  # noqa: B010 -- preserve source assignment semantics
        return channel_name


async def delete_channel_name(channel_name_id: int) -> None:
    """删除 ChannelName。"""
    async with db_manager.get_session() as session:
        channel_name = await session.get(ChannelName, channel_name_id)
        if channel_name is not None:
            await session.delete(channel_name)


async def get_catalog_channel(catalog_channel_id: int) -> CatalogChannel | None:
    """读取单个 CatalogChannel。"""
    async with db_manager.get_read_session() as session:
        return await session.get(CatalogChannel, catalog_channel_id)


async def delete_catalog_channel(catalog_channel_id: int) -> None:
    """删除 CatalogChannel。"""
    async with db_manager.get_session() as session:
        catalog_channel = await session.get(CatalogChannel, catalog_channel_id)
        if catalog_channel is not None:
            await session.delete(catalog_channel)


async def get_catalog_feed(catalog_feed_id: int) -> CatalogFeed | None:
    """读取单个 CatalogFeed，并预加载编辑表单和页面提示需要的关联对象。"""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(CatalogFeed)
            .options(selectinload(CatalogFeed.catalog_channel), selectinload(CatalogFeed.logo_asset))
            .where(CatalogFeed.id == catalog_feed_id)
        )
        return result.scalar_one_or_none()


async def delete_catalog_feed(catalog_feed_id: int) -> None:
    """删除 CatalogFeed。"""
    async with db_manager.get_session() as session:
        catalog_feed = await session.get(CatalogFeed, catalog_feed_id)
        if catalog_feed is not None:
            await session.delete(catalog_feed)


async def get_upstream_record(record_id: int) -> UpstreamSourceRecord | None:
    """读取单个上游原始记录，供按需 raw payload om-modal 使用。"""
    async with db_manager.get_read_session() as session:
        return await session.get(UpstreamSourceRecord, record_id)


async def get_logo_asset(logo_asset_id: int) -> CatalogLogoAsset | None:
    """读取单个 LogoAsset 及其 feed，供按需图片对比 om-modal 使用。"""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(CatalogLogoAsset)
            .where(CatalogLogoAsset.id == logo_asset_id)
            .options(selectinload(CatalogLogoAsset.catalog_feed))
        )
        return result.scalar_one_or_none()


async def get_match_decision(decision_id: int) -> CatalogMatchDecision | None:
    """读取单个人工匹配决策，并预加载弹窗标题需要的关联对象。"""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(CatalogMatchDecision)
            .where(CatalogMatchDecision.id == decision_id)
            .options(
                selectinload(CatalogMatchDecision.source_record),
                selectinload(CatalogMatchDecision.catalog_channel),
                selectinload(CatalogMatchDecision.catalog_feed),
            )
        )
        return result.scalar_one_or_none()


async def catalog_channel_feed_counts(channel_ids: list[int], *, session: Any | None = None) -> dict[int, int]:
    """一次聚合读取每个 CatalogChannel 的 feed 数量，避免列表 N+1。"""
    if not channel_ids:
        return {}

    async def load_counts(active_session: Any) -> dict[int, int]:
        """在指定 session 中执行聚合查询。"""
        result = await active_session.execute(
            select(CatalogFeed.catalog_channel_id, func.count(CatalogFeed.id))
            .where(CatalogFeed.catalog_channel_id.in_(channel_ids))
            .group_by(CatalogFeed.catalog_channel_id)
        )
        return {int(channel_id): int(count or 0) for channel_id, count in result.all()}

    if session is not None:
        return await load_counts(session)
    async with db_manager.get_read_session() as active_session:
        return await load_counts(active_session)


async def channel_name_reference_defaults() -> dict[str, int | None]:
    """读取 ChannelName 表单需要的旧外键默认值，避免新建页使用不存在的硬编码 ID。"""
    defaults: dict[str, int | None] = {}
    async with db_manager.get_read_session() as session:
        for field_name, table_name in CHANNEL_NAME_REFERENCE_TABLES.items():
            try:
                value = (
                    await session.execute(
                        text(f"SELECT id FROM {table_name} ORDER BY id LIMIT 1")
                    )
                ).scalar_one_or_none()
            except SQLAlchemyError:
                value = None
            if value is None:
                try:
                    value = (
                        await session.execute(
                            text(f"SELECT {field_name} FROM epg_channelname WHERE {field_name} IS NOT NULL ORDER BY {field_name} LIMIT 1")
                        )
                    ).scalar_one_or_none()
                except SQLAlchemyError:
                    value = None
            defaults[field_name] = int(value) if value is not None else None
    return defaults


async def channel_name_reference_ids_exist(reference_ids: dict[str, int | None]) -> dict[str, bool]:
    """校验 ChannelName 的旧外键 ID 是否存在，把数据库约束错误前置为表单错误。"""
    result: dict[str, bool] = {}
    async with db_manager.get_read_session() as session:
        for field_name, table_name in CHANNEL_NAME_REFERENCE_TABLES.items():
            value = reference_ids.get(field_name)
            if value is None:
                result[field_name] = False
                continue
            try:
                exists = (
                    await session.execute(
                        text(f"SELECT 1 FROM {table_name} WHERE id = :value LIMIT 1"),
                        {"value": int(value)},
                    )
                ).scalar_one_or_none()
            except SQLAlchemyError:
                result[field_name] = True
                continue
            except (TypeError, ValueError):
                exists = None
            result[field_name] = exists is not None
    return result


async def channel_choices(limit: int = 200) -> list[ChannelsEpg]:
    """返回表单频道下拉候选。"""
    async with db_manager.get_read_session() as session:
        result = await session.execute(select(ChannelsEpg).order_by(ChannelsEpg.name.asc()).limit(limit))
        return list(result.scalars().all())


async def channel_select_choices(limit: int = 200) -> list[SelectChoice]:
    """返回频道 select 候选。"""
    channels = await channel_choices(limit)
    return [SelectChoice(id=channel.id, text=channel.name) for channel in channels]


async def dashboard_stats() -> dict[str, Any]:
    """返回后台首页真实统计数据。"""
    async with db_manager.get_read_session() as session:
        channel_count = int((await session.execute(select(func.count(ChannelsEpg.id)))).scalar_one() or 0)
        epg_count = int((await session.execute(select(func.count(EpgList.id)))).scalar_one() or 0)
        catalog_channel_count = int((await session.execute(select(func.count(CatalogChannel.id)))).scalar_one() or 0)
        catalog_feed_count = int((await session.execute(select(func.count(CatalogFeed.id)))).scalar_one() or 0)
        upstream_record_count = int((await session.execute(select(func.count(UpstreamSourceRecord.id)))).scalar_one() or 0)
        logo_asset_count = int((await session.execute(select(func.count(CatalogLogoAsset.id)))).scalar_one() or 0)
        pending_decision_count = int(
            (
                await session.execute(
                    select(func.count(CatalogMatchDecision.id)).where(CatalogMatchDecision.decision == "manual_review")
                )
            ).scalar_one()
            or 0
        )
        country_count = int((await session.execute(select(func.count(func.distinct(ChannelsEpg.country))))).scalar_one() or 0)
        latest_epg = (await session.execute(select(func.max(EpgList.start_date)))).scalar_one_or_none()
        latest_sync = (await session.execute(select(func.max(UpstreamSourceRecord.last_seen_at)))).scalar_one_or_none()
        recent_anomaly_records = (
            (
                await session.execute(
                    select(UpstreamSourceRecord)
                    .where(UpstreamSourceRecord.disappeared_at.isnot(None))
                    .order_by(UpstreamSourceRecord.disappeared_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        recent_decisions = (
            (
                await session.execute(
                    select(CatalogMatchDecision)
                    .where(CatalogMatchDecision.decision == "manual_review")
                    .order_by(CatalogMatchDecision.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        logo_coverage_percent = round((logo_asset_count / catalog_feed_count) * 100, 1) if catalog_feed_count else 0
        return {
            "channel_count": channel_count,
            "epg_count": epg_count,
            "catalog_channel_count": catalog_channel_count,
            "catalog_feed_count": catalog_feed_count,
            "upstream_record_count": upstream_record_count,
            "pending_decision_count": pending_decision_count,
            "logo_asset_count": logo_asset_count,
            "logo_coverage_percent": logo_coverage_percent,
            "country_count": country_count,
            "latest_epg": latest_epg,
            "latest_sync": latest_sync,
            "recent_anomaly_records": list(recent_anomaly_records),
            "recent_decisions": list(recent_decisions),
        }


async def dashboard_notifications(limit: int = 8) -> list[dict[str, Any]]:
    """返回顶栏真实业务通知项。"""
    items = await notification_items(limit=limit)
    return [item.as_topbar_dict() for item in items]


async def notification_items(limit: int = 100) -> list[NotificationItem]:
    """实时计算通知中心列表数据，不写入数据库。"""
    async with db_manager.get_read_session() as session:
        decisions = (
            (
                await session.execute(
                    select(CatalogMatchDecision)
                    .where(CatalogMatchDecision.decision == "manual_review")
                    .order_by(CatalogMatchDecision.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        logos = (
            (
                await session.execute(
                    select(CatalogLogoAsset)
                    .where(CatalogLogoAsset.quality_score < 70)
                    .order_by(CatalogLogoAsset.updated_at.desc(), CatalogLogoAsset.quality_score.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        upstream_records = (
            (
                await session.execute(
                    select(UpstreamSourceRecord)
                    .where(UpstreamSourceRecord.disappeared_at.isnot(None))
                    .order_by(UpstreamSourceRecord.disappeared_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    items: list[NotificationItem] = []
    for decision in decisions:
        items.append(
            NotificationItem(
                id=f"decision:{int(decision.id)}",
                notification_type="decision",
                severity="critical",
                title=_("Manual review decision"),
                description=_(
                    "%(scope)s: %(reason)s",
                    scope=_((decision.decision_scope or "unknown").replace("_", " ").title()),
                    reason=decision.reason,
                ),
                created_at=decision.created_at,
                href=f"/match-decisions?{urlencode({'decision': 'manual_review', 'decided_by': decision.decided_by or ''})}",
                icon="ri-error-warning-line",
                tone="danger",
                source_label=_("Decision #%(id)s", id=int(decision.id)),
                source_model="CatalogMatchDecision",
                source_id=int(decision.id),
            )
        )
    for logo in logos:
        items.append(
            NotificationItem(
                id=f"logo:{int(logo.id)}",
                notification_type="logo",
                severity="warning" if int(logo.quality_score or 0) >= 40 else "critical",
                title=_("Low quality logo"),
                description=_("Score %(score)s · feed #%(feed_id)s", score=logo.quality_score, feed_id=logo.catalog_feed_id),
                created_at=logo.updated_at,
                href=f"/logo-assets?quality_max={int(logo.quality_score or 0)}",
                icon="ri-image-line",
                tone="warning",
                source_label=_("Logo #%(id)s", id=int(logo.id)),
                source_model="CatalogLogoAsset",
                source_id=int(logo.id),
            )
        )
    for record in upstream_records:
        items.append(
            NotificationItem(
                id=f"upstream:{int(record.id)}",
                notification_type="upstream",
                severity="info",
                title=_("Upstream disappeared"),
                description=record.primary_name or record.source_record_key,
                created_at=record.disappeared_at,
                href=f"/upstream-records?{urlencode({'status': record.status or ''})}",
                icon="ri-cloud-line",
                tone="info",
                source_label=f"{record.source_code}:{record.source_record_key}",
                source_model="UpstreamSourceRecord",
                source_id=int(record.id),
            )
        )
    return sorted(items, key=lambda item: str(item.created_at or ""), reverse=True)[:limit]


async def notification_stats() -> dict[str, int]:
    """返回通知中心统计卡片数据。"""
    items = await notification_items(limit=NOTIFICATION_CENTER_LIMIT)
    return {
        "pending": len(items),
        "critical": sum(1 for item in items if item.severity == "critical"),
        "warning": sum(1 for item in items if item.severity == "warning"),
        "info": sum(1 for item in items if item.severity == "info"),
    }


async def upstream_record_facets(limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    """读取上游审计页本地列表需要的来源和状态聚合。"""
    async with db_manager.get_read_session() as session:
        source_rows = (
            await session.execute(
                select(
                    UpstreamSourceRecord.source_code,
                    func.count(UpstreamSourceRecord.id),
                    func.max(UpstreamSourceRecord.last_seen_at),
                )
                .group_by(UpstreamSourceRecord.source_code)
                .order_by(func.count(UpstreamSourceRecord.id).desc(), UpstreamSourceRecord.source_code.asc())
                .limit(limit)
            )
        ).all()
        status_rows = (
            await session.execute(
                select(
                    UpstreamSourceRecord.status,
                    func.count(UpstreamSourceRecord.id),
                    func.max(UpstreamSourceRecord.last_seen_at),
                )
                .group_by(UpstreamSourceRecord.status)
                .order_by(func.count(UpstreamSourceRecord.id).desc(), UpstreamSourceRecord.status.asc())
                .limit(limit)
            )
        ).all()

    def normalize(rows: Sequence[Sequence[Any]], *, query_name: str) -> list[dict[str, Any]]:
        """把聚合行转换为模板可直接渲染的数据结构。"""
        return [
            {
                "label": str(value or _("Unknown")),
                "count": int(count or 0),
                "updated_at": updated_at,
                "href": f"/upstream-records?{urlencode({query_name: str(value or '')})}",
            }
            for value, count, updated_at in rows
        ]

    return {
        "sources": normalize(source_rows, query_name="source_code"),
        "statuses": normalize(status_rows, query_name="status"),
    }
