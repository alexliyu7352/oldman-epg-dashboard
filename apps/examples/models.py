"""Real database models used by the Dashboard feature examples."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from oldman.db.models import DatabaseModel
from oldman.i18n import gettext_lazy as _
from oldman.storage import file_column


class ExampleTeam(DatabaseModel):
    """Team used to group projects in table and form examples."""

    __tablename__ = "example_team"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("slug", name="uq_example_team_slug"),
        Index("ix_example_team_region", "region"),
        Index("ix_example_team_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    projects: Mapped[list[ExampleProject]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
    )

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Team")
        verbose_name_plural = _("Example Teams")


class ExampleProject(DatabaseModel):
    """Project covering common fields, filters, relations and metadata text."""

    __tablename__ = "example_project"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("slug", name="uq_example_project_slug"),
        CheckConstraint("budget >= 0", name="ck_example_project_budget"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_example_project_progress"),
        Index("ix_example_project_team_status", "team_id", "status"),
        Index("ix_example_project_priority", "priority"),
        Index("ix_example_project_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("example_team.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#405189")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    team: Mapped[ExampleTeam] = relationship(back_populates="projects")
    tasks: Mapped[list[ExampleTask]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tag_links: Mapped[list[ExampleProjectTag]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assets: Mapped[list[ExampleAsset]] = relationship(back_populates="project")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Project")
        verbose_name_plural = _("Example Projects")


class ExampleTask(DatabaseModel):
    """Sortable project task used by CRUD and drag-and-drop examples."""

    __tablename__ = "example_task"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_example_task_position"),
        CheckConstraint("estimated_hours IS NULL OR estimated_hours >= 0", name="ck_example_task_estimated_hours"),
        Index("ix_example_task_project_status_position", "project_id", "status", "position"),
        Index("ix_example_task_priority", "priority"),
        Index("ix_example_task_due_at", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("example_project.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="todo")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    project: Mapped[ExampleProject] = relationship(back_populates="tasks")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Task")
        verbose_name_plural = _("Example Tasks")


class ExampleTag(DatabaseModel):
    """Reusable project tag for many-to-many examples."""

    __tablename__ = "example_tag"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("name", name="uq_example_tag_name"),
        UniqueConstraint("slug", name="uq_example_tag_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)

    project_links: Mapped[list[ExampleProjectTag]] = relationship(back_populates="tag", cascade="all, delete-orphan")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Tag")
        verbose_name_plural = _("Example Tags")


class ExampleProjectTag(DatabaseModel):
    """Explicit project-to-tag association retained as a fixture model."""

    __tablename__ = "example_project_tag"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("project_id", "tag_id", name="uq_example_project_tag_pair"),
        Index("ix_example_project_tag_tag_id", "tag_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("example_project.id", ondelete="CASCADE"), nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("example_tag.id", ondelete="CASCADE"), nullable=False)

    project: Mapped[ExampleProject] = relationship(back_populates="tag_links")
    tag: Mapped[ExampleTag] = relationship(back_populates="project_links")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Project Tag")
        verbose_name_plural = _("Example Project Tags")


class ExampleAsset(DatabaseModel):
    """Uploaded document and preview exercising multiple managed file fields."""

    __tablename__ = "example_asset"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (Index("ix_example_asset_project_id", "project_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("example_project.id", ondelete="SET NULL"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    document_path: Mapped[str] = file_column(upload_to="examples/assets", storage="default")
    preview_path: Mapped[str | None] = file_column(upload_to="examples/previews", storage="default", nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    project: Mapped[ExampleProject | None] = relationship(back_populates="assets")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Asset")
        verbose_name_plural = _("Example Assets")


class ExampleServer(DatabaseModel):
    """Stable server inventory paired with time-series display metrics."""

    __tablename__ = "example_server"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("name", name="uq_example_server_name"),
        CheckConstraint("cpu_cores > 0", name="ck_example_server_cpu_cores"),
        CheckConstraint("memory_gb > 0", name="ck_example_server_memory_gb"),
        CheckConstraint("bandwidth_mbps > 0", name="ck_example_server_bandwidth"),
        Index("ix_example_server_region", "region"),
        Index("ix_example_server_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    cpu_cores: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    memory_gb: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    bandwidth_mbps: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    metrics: Mapped[list[ExampleServerMetric]] = relationship(back_populates="server", cascade="all, delete-orphan")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Server")
        verbose_name_plural = _("Example Servers")


class ExampleServerMetric(DatabaseModel):
    """Timestamped server values used by charts and SSE table updates."""

    __tablename__ = "example_server_metric"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        CheckConstraint("cpu_percent >= 0 AND cpu_percent <= 100", name="ck_example_metric_cpu"),
        CheckConstraint("memory_percent >= 0 AND memory_percent <= 100", name="ck_example_metric_memory"),
        CheckConstraint("upload_mbps >= 0", name="ck_example_metric_upload"),
        CheckConstraint("download_mbps >= 0", name="ck_example_metric_download"),
        Index("ix_example_server_metric_server_sampled", "server_id", "sampled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("example_server.id", ondelete="CASCADE"), nullable=False)
    sampled_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    cpu_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    memory_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    upload_mbps: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    download_mbps: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    server: Mapped[ExampleServer] = relationship(back_populates="metrics")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Server Metric")
        verbose_name_plural = _("Example Server Metrics")


class ExampleLogo(DatabaseModel):
    """Searchable committed SVG option for rich remote selects."""

    __tablename__ = "example_logo"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("slug", name="uq_example_logo_slug"),
        Index("ix_example_logo_name", "name"),
        Index("ix_example_logo_country_available", "country_code", "is_available"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    svg_path: Mapped[str] = mapped_column(String(255), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    stream_profiles: Mapped[list[ExampleStreamProfile]] = relationship(back_populates="logo")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Logo")
        verbose_name_plural = _("Example Logos")


class ExampleStreamProfile(DatabaseModel):
    """Stream configuration whose variable sources are encoded as JSON text."""

    __tablename__ = "example_stream_profile"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override
    __table_args__ = (
        UniqueConstraint("name", name="uq_example_stream_profile_name"),
        Index("ix_example_stream_profile_logo_id", "logo_id"),
        Index("ix_example_stream_profile_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("example_logo.id", ondelete="SET NULL"), nullable=True)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    logo: Mapped[ExampleLogo | None] = relationship(back_populates="stream_profiles")

    class Meta:
        """Provide labels for automatic Admin presentation."""

        verbose_name = _("Example Stream Profile")
        verbose_name_plural = _("Example Stream Profiles")


__all__ = [
    "ExampleAsset",
    "ExampleLogo",
    "ExampleProject",
    "ExampleProjectTag",
    "ExampleServer",
    "ExampleServerMetric",
    "ExampleStreamProfile",
    "ExampleTag",
    "ExampleTask",
    "ExampleTeam",
]
