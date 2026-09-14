"""旧 EPG 业务表的后台管理模型。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from oldman.db.models import DatabaseModel
from oldman.i18n import gettext_lazy as _

MYSQL_CASE_SENSITIVE_COLLATION = "utf8mb4_bin"
MYSQL_CASE_INSENSITIVE_COLLATION = "utf8mb4_unicode_ci"


def mysql_collated_string(length: int, collation: str) -> String:
    """仅在 MySQL 方言启用指定 collation，避免 SQLite 测试库建表失败。"""
    return String(length).with_variant(String(length, collation=collation), "mysql")


class ChannelsEpg(DatabaseModel):
    """旧 Django `epg_channelsepg` 表的后台管理映射。"""

    __tablename__ = "epg_channelsepg"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    src_url: Mapped[str] = mapped_column(String(200), index=True)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=0, index=True)
    last_date: Mapped[dt.date] = mapped_column(Date, index=True)
    create_date: Mapped[dt.datetime] = mapped_column(DateTime)
    tvg_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    tvg_id_lookup: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    tvg_id_source: Mapped[str] = mapped_column(String(32), default="unresolved")
    tvg_id_confidence: Mapped[int] = mapped_column(SmallInteger, default=0)
    tvg_id_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    tvg_id_resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    tvg_id_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    epg_items: Mapped[list[EpgList]] = relationship(back_populates="channel")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("EPG Channel")
        verbose_name_plural = _("EPG Channels")


class EpgList(DatabaseModel):
    """旧 Django `epg_epglist` 表的后台管理映射。"""

    __tablename__ = "epg_epglist"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("epg_channelsepg.id"), index=True)
    start_date: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    create_date: Mapped[dt.datetime] = mapped_column(DateTime)

    channel: Mapped[ChannelsEpg] = relationship(back_populates="epg_items")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("EPG Programme")
        verbose_name_plural = _("EPG Programmes")


class ChannelName(DatabaseModel):
    """旧 Django `epg_channelname` 表的后台只读统计映射。"""

    __tablename__ = "epg_channelname"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name_cn: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name_tw: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    name_es: Mapped[str | None] = mapped_column(String(50), nullable=True)
    logo: Mapped[str] = mapped_column(String(100), nullable=False)
    logo_size: Mapped[Decimal | None] = mapped_column(Numeric(64, 2), nullable=True)
    epg_id: Mapped[int | None] = mapped_column(ForeignKey("epg_channelsepg.id"), nullable=True, index=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_order: Mapped[int] = mapped_column(Integer, default=9999, index=True)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    country_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    language_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    create_date: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Channel Name")
        verbose_name_plural = _("Channel Names")


class UpstreamSourceRecord(DatabaseModel):
    """上游原始记录镜像，用于首页同步状态和来源质量统计。"""

    __tablename__ = "upstream_source_record"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("source_code", "source_record_key", name="uq_upstream_source_record_source_key"),
        Index("ix_upstream_source_record_catalog_feed_id", "catalog_feed_id"),
        Index("ix_upstream_source_record_source_status", "source_code", "status"),
        Index("ix_upstream_source_record_last_seen_at", "last_seen_at"),
        Index("ix_upstream_source_record_raw_hash", "raw_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_feed_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_feed.id", ondelete="SET NULL"), nullable=True)
    source_code: Mapped[str] = mapped_column(mysql_collated_string(64, MYSQL_CASE_SENSITIVE_COLLATION), nullable=False)
    source_record_key: Mapped[str] = mapped_column(mysql_collated_string(255, MYSQL_CASE_SENSITIVE_COLLATION), nullable=False)
    record_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    primary_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_urls_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    disappeared_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    catalog_feed: Mapped[CatalogFeed | None] = relationship(back_populates="source_records")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Upstream Record")
        verbose_name_plural = _("Upstream Records")


class CatalogChannel(DatabaseModel):
    """频道身份分组，只回答“这是谁”。"""

    __tablename__ = "catalog_channel"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("channel_id", name="uq_catalog_channel_channel_id"),
        Index("ix_catalog_channel_channel_key", "channel_key"),
        Index("ix_catalog_channel_owner_country_code", "owner_country_code"),
        Index("ix_catalog_channel_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    channel_id: Mapped[str] = mapped_column(mysql_collated_string(160, MYSQL_CASE_INSENSITIVE_COLLATION), nullable=False)
    identity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="provisional")
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    feeds: Mapped[list[CatalogFeed]] = relationship(back_populates="catalog_channel")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Catalog Channel")
        verbose_name_plural = _("Catalog Channels")


class CatalogFeed(DatabaseModel):
    """频道真实播出版本，是后台 dashboard 的 feed 统计来源。"""

    __tablename__ = "catalog_feed"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("tvg_id", name="uq_catalog_feed_tvg_id"),
        UniqueConstraint("catalog_channel_id", "feed_suffix", name="uq_catalog_feed_channel_suffix"),
        Index("ix_catalog_feed_catalog_channel_id", "catalog_channel_id"),
        Index("ix_catalog_feed_wikidata_qid", "wikidata_qid"),
        Index("ix_catalog_feed_status", "status"),
        Index("ix_catalog_feed_service_country_code", "service_country_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_channel_id: Mapped[int] = mapped_column(ForeignKey("catalog_channel.id", ondelete="CASCADE"), nullable=False)
    feed_suffix: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tvg_id: Mapped[str] = mapped_column(mysql_collated_string(240, MYSQL_CASE_INSENSITIVE_COLLATION), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    accepted_names_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    compatible_tvg_ids_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    region_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language_hints_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone_hints_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    wikidata_qid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    wikipedia_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    official_website: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description_source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description_source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="provisional")
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    catalog_channel: Mapped[CatalogChannel] = relationship(back_populates="feeds")
    source_records: Mapped[list[UpstreamSourceRecord]] = relationship(back_populates="catalog_feed")
    logo_asset: Mapped[CatalogLogoAsset | None] = relationship(back_populates="catalog_feed")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Catalog Feed")
        verbose_name_plural = _("Catalog Feeds")


class CatalogLogoAsset(DatabaseModel):
    """feed 级当前 logo，供 dashboard 统计覆盖率和质量分布。"""

    __tablename__ = "catalog_logo_asset"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("catalog_feed_id", name="uq_catalog_logo_asset_feed"),
        Index("ix_catalog_logo_asset_sha256", "sha256"),
        Index("ix_catalog_logo_asset_phash", "phash"),
        Index("ix_catalog_logo_asset_edge_phash", "edge_phash"),
        Index("ix_catalog_logo_asset_mask_phash", "mask_phash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_feed_id: Mapped[int] = mapped_column(ForeignKey("catalog_feed.id", ondelete="CASCADE"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_path: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_path: Mapped[str] = mapped_column(String(512), nullable=False)
    preview_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    edge_map_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mask_map_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dhash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    edge_phash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mask_phash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    visible_bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    catalog_feed: Mapped[CatalogFeed] = relationship(back_populates="logo_asset")

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Logo Asset")
        verbose_name_plural = _("Logo Assets")


class CatalogMatchDecision(DatabaseModel):
    """频道库编译和人工闭环审计，首页只读取待处理规模。"""

    __tablename__ = "catalog_match_decision"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        Index("ix_catalog_match_decision_scope", "decision_scope"),
        Index("ix_catalog_match_decision_source_record_id", "source_record_id"),
        Index("ix_catalog_match_decision_catalog_channel_id", "catalog_channel_id"),
        Index("ix_catalog_match_decision_catalog_feed_id", "catalog_feed_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("upstream_source_record.id", ondelete="SET NULL"), nullable=True)
    catalog_channel_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_channel.id", ondelete="SET NULL"), nullable=True)
    catalog_feed_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_feed.id", ondelete="SET NULL"), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    source_record: Mapped[UpstreamSourceRecord | None] = relationship()
    catalog_channel: Mapped[CatalogChannel | None] = relationship()
    catalog_feed: Mapped[CatalogFeed | None] = relationship()

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("Match Decision")
        verbose_name_plural = _("Match Decisions")
