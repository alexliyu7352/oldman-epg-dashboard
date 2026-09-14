"""Database queries shared by Dashboard example views."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from oldman.db import db_manager
from oldman.serializers import MsgspecModel
from oldman.storage import storages

from .models import (
    ExampleAsset,
    ExampleProject,
    ExampleServer,
    ExampleServerMetric,
    ExampleStreamProfile,
    ExampleTask,
)

STORAGE_API_PREFIX = "examples/storage-api"
SORTABLE_TASK_STATUSES = ("todo", "in_progress", "review", "done")
SORTABLE_TASK_LIMIT = 12


class SortableTaskMoveRejected(ValueError):
    """A visible task move cannot be applied to the current board state."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _RealtimeTableRow(MsgspecModel, kw_only=True):
    """One visible server row update for the private Demo stream."""

    id: int
    cells: dict[str, str]


class _RealtimeTablePayload(MsgspecModel, kw_only=True):
    """Current metric values keyed by the Table's stable DOM attributes."""

    rows: list[_RealtimeTableRow]


class RealtimeChartPayload(MsgspecModel, kw_only=True, frozen=True):
    """One persisted server sample delivered to the realtime chart."""

    server_id: int
    sampled_at: str
    cpu_percent: float
    memory_percent: float
    upload_mbps: float
    download_mbps: float
    source: str


async def list_assets(limit: int = 20) -> list[ExampleAsset]:
    """Return recent uploaded assets for the Storage example pages."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(ExampleAsset).order_by(ExampleAsset.id.desc()).limit(limit)
        )
        return list(result.scalars())


async def asset_file_states(asset: ExampleAsset) -> dict[str, dict[str, object]]:
    """Read the real Storage state of both logical file names."""
    storage = storages.using("default")
    states: dict[str, dict[str, object]] = {}
    for field_name in ("document_path", "preview_path"):
        path = getattr(asset, field_name)
        exists = bool(path) and await storage.exists(path)
        info = await storage.stat(path) if exists else None
        states[field_name] = {
            "exists": exists,
            "info": info,
            "path": path,
            "url": f"/media/{quote(path, safe='/')}" if exists else None,
        }
    return states


async def run_storage_api_demo() -> dict[str, object]:
    """Exercise the public Storage API under one fixed Demo-owned prefix."""
    storage = storages.using("default")
    requested_name = f"{STORAGE_API_PREFIX}/sample.txt"
    await storage.delete(requested_name)
    saved_names: list[str] = []
    try:
        saved_name = await storage.save(requested_name, b"first Storage API demo value")
        saved_names.append(saved_name)
        collision_name = await storage.save(
            requested_name, b"collision Storage API demo value"
        )
        saved_names.append(collision_name)
        overwritten_name = await storage.save(
            requested_name, b"overwritten by the Storage API demo", overwrite=True
        )
        exists_before_delete = await storage.exists(saved_name)
        info = await storage.stat(saved_name)
        async with await storage.open(saved_name) as stored:
            content = (await stored.read()).decode("utf-8")
        await storage.delete(saved_name)
        await storage.delete(collision_name)
        exists_after_delete = await storage.exists(saved_name)
        return {
            "collision_name": collision_name,
            "content": content,
            "exists_after_delete": exists_after_delete,
            "exists_before_delete": exists_before_delete,
            "info": info,
            "overwritten_name": overwritten_name,
            "saved_name": saved_name,
        }
    finally:
        for name in saved_names:
            await storage.delete(name)


async def list_stream_profiles(limit: int = 12) -> list[ExampleStreamProfile]:
    """Return deterministic StreamProfile rows for the JSON list example."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(ExampleStreamProfile)
            .order_by(ExampleStreamProfile.id.asc())
            .limit(limit)
        )
        return list(result.scalars())


async def list_projects(limit: int = 12) -> list[ExampleProject]:
    """Return deterministic database rows for the static Table examples."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(ExampleProject)
            .options(selectinload(ExampleProject.team))
            .order_by(ExampleProject.id.asc())
            .limit(limit)
        )
        return list(result.scalars())


async def list_sortable_tasks() -> dict[str, list[ExampleTask]]:
    """Return the fixed real-data task board used by the Sortable example."""
    async with db_manager.get_read_session() as session:
        tasks = await _sortable_tasks(session)
    return _group_sortable_tasks(tasks)


async def move_sortable_task(
    session: AsyncSession,
    *,
    item_id: int,
    source_status: str,
    target_status: str,
    target_position: int,
) -> None:
    """Move one visible task and normalize the affected board positions."""
    if (
        source_status not in SORTABLE_TASK_STATUSES
        or target_status not in SORTABLE_TASK_STATUSES
    ):
        raise SortableTaskMoveRejected("invalid_status")

    tasks = await _sortable_tasks(session)
    task = next((candidate for candidate in tasks if candidate.id == item_id), None)
    if task is None:
        raise SortableTaskMoveRejected("not_found")
    if task.status != source_status:
        raise SortableTaskMoveRejected("stale_source")
    if task.priority == "critical" and target_status == "done":
        raise SortableTaskMoveRejected("critical_done")

    lanes = _group_sortable_tasks(tasks)
    source = lanes[source_status]
    target = lanes[target_status]
    source.remove(task)
    if source is target:
        target.insert(min(max(target_position, 0), len(target)), task)
        _set_task_positions(target)
    else:
        target.insert(min(max(target_position, 0), len(target)), task)
        task.status = target_status
        _set_task_positions(source)
        _set_task_positions(target)
    task.is_completed = target_status == "done"


async def _sortable_tasks(session: AsyncSession) -> list[ExampleTask]:
    """Load the deterministic first task records and their project labels."""
    result = await session.execute(
        select(ExampleTask)
        .options(selectinload(ExampleTask.project))
        .order_by(ExampleTask.id.asc())
        .limit(SORTABLE_TASK_LIMIT)
    )
    return list(result.scalars().all())


def _group_sortable_tasks(tasks: Sequence[ExampleTask]) -> dict[str, list[ExampleTask]]:
    """Group visible tasks into stable status lanes."""
    lanes = {status: [] for status in SORTABLE_TASK_STATUSES}
    for task in tasks:
        if task.status in lanes:
            lanes[task.status].append(task)
    for lane in lanes.values():
        lane.sort(key=lambda task: (task.position, task.id))
    return lanes


def _set_task_positions(tasks: Sequence[ExampleTask]) -> None:
    """Persist the visible order as consecutive zero-based positions."""
    for position, task in enumerate(tasks):
        task.position = position


async def list_server_metric_snapshots() -> (
    list[list[tuple[ExampleServer, ExampleServerMetric]]]
):
    """Replay the latest 60 complete batches, excluding single-server chart samples."""
    async with db_manager.get_read_session() as session:
        server_ids = set(
            await session.scalars(select(ExampleServerMetric.server_id).distinct())
        )
        if not server_ids:
            return []
        # Filter completeness before limiting; chart samples must not evict replay batches.
        recent_times = (
            select(ExampleServerMetric.sampled_at)
            .group_by(ExampleServerMetric.sampled_at)
            .having(func.count(func.distinct(ExampleServerMetric.server_id)) == len(server_ids))
            .order_by(ExampleServerMetric.sampled_at.desc())
            .limit(60)
        )
        result = await session.execute(
            select(ExampleServer, ExampleServerMetric)
            .join(
                ExampleServerMetric, ExampleServerMetric.server_id == ExampleServer.id
            )
            .where(ExampleServerMetric.sampled_at.in_(recent_times))
            .order_by(ExampleServerMetric.sampled_at.asc(), ExampleServer.id.asc())
        )

    grouped: dict[dt.datetime, list[tuple[ExampleServer, ExampleServerMetric]]] = {}
    for row in result.all():
        server, metric = row
        grouped.setdefault(metric.sampled_at, []).append((server, metric))
    return list(grouped.values())


async def list_latest_server_metrics() -> list[tuple[ExampleServer, ExampleServerMetric]]:
    """Return the newest persisted metric for every server."""
    latest_metric_id = (
        select(ExampleServerMetric.id)
        .where(ExampleServerMetric.server_id == ExampleServer.id)
        .order_by(ExampleServerMetric.id.desc())
        .limit(1)
        .correlate(ExampleServer)
        .scalar_subquery()
    )
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(ExampleServer, ExampleServerMetric)
            .join(ExampleServerMetric, ExampleServerMetric.id == latest_metric_id)
            .order_by(ExampleServer.id)
        )
    return list(result.tuples())


async def list_servers() -> list[ExampleServer]:
    """Return the stable server inventory used by realtime examples."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(select(ExampleServer).order_by(ExampleServer.id))
    return list(result.scalars())


async def list_server_metrics(
    server_id: int, *, limit: int = 20
) -> list[ExampleServerMetric]:
    """Return one server's recent samples in chronological order."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(
            select(ExampleServerMetric)
            .where(ExampleServerMetric.server_id == server_id)
            .order_by(ExampleServerMetric.sampled_at.desc())
            .limit(limit)
        )
    return list(reversed(result.scalars().all()))


async def create_server_metric(
    session: AsyncSession, server_id: int
) -> RealtimeChartPayload:
    """Persist one deterministic next sample before it is published through Redis."""
    server = await session.get(ExampleServer, server_id)
    if server is None:
        raise ValueError("server_not_found")
    result = await session.execute(
        select(ExampleServerMetric)
        .where(ExampleServerMetric.server_id == server_id)
        .order_by(ExampleServerMetric.sampled_at.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    sampled_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    if latest is not None:
        sampled_at = max(sampled_at, latest.sampled_at + dt.timedelta(minutes=1))
    step = (latest.id if latest is not None else server_id) % 7 + 1
    metric = ExampleServerMetric(
        server_id=server_id,
        sampled_at=sampled_at,
        cpu_percent=_next_metric(
            latest.cpu_percent if latest else Decimal("18"), step * 2.25, 96
        ),
        memory_percent=_next_metric(
            latest.memory_percent if latest else Decimal("32"), step * 1.5, 94
        ),
        upload_mbps=_next_metric(
            latest.upload_mbps if latest else Decimal("40"),
            step * 4.75,
            float(server.bandwidth_mbps),
        ),
        download_mbps=_next_metric(
            latest.download_mbps if latest else Decimal("55"),
            step * 5.25,
            float(server.bandwidth_mbps),
        ),
    )
    session.add(metric)
    await session.flush()
    return realtime_chart_payload(metric, source="redis")


def realtime_chart_payload(
    metric: ExampleServerMetric, *, source: str
) -> RealtimeChartPayload:
    """Convert one database model into the stable browser event contract."""
    return RealtimeChartPayload(
        server_id=metric.server_id,
        sampled_at=metric.sampled_at.isoformat(),
        cpu_percent=float(metric.cpu_percent),
        memory_percent=float(metric.memory_percent),
        upload_mbps=float(metric.upload_mbps),
        download_mbps=float(metric.download_mbps),
        source=source,
    )


def realtime_chart_stream(server_id: int) -> str:
    """Return the validated business stream name for one server."""
    return f"examples.monitoring.server-{server_id}"


def _next_metric(current: Decimal, increment: float, ceiling: float) -> Decimal:
    value = (float(current) + increment) % max(ceiling, 1)
    return Decimal(f"{max(value, 1):.2f}")


def _realtime_payload(
    rows: Sequence[tuple[ExampleServer, ExampleServerMetric]],
) -> _RealtimeTablePayload:
    """Format database metrics exactly as the visible Table cells expect."""
    return _RealtimeTablePayload(
        rows=[
            _RealtimeTableRow(
                id=server.id,
                cells={
                    "sampled_at": metric.sampled_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "cpu_percent": f"{metric.cpu_percent:.2f}%",
                    "memory_percent": f"{metric.memory_percent:.2f}%",
                    "upload_mbps": f"{metric.upload_mbps:.2f} Mbps",
                    "download_mbps": f"{metric.download_mbps:.2f} Mbps",
                },
            )
            for server, metric in rows
        ]
    )


__all__ = [
    "RealtimeChartPayload",
    "STORAGE_API_PREFIX",
    "SORTABLE_TASK_STATUSES",
    "SortableTaskMoveRejected",
    "asset_file_states",
    "create_server_metric",
    "list_assets",
    "list_projects",
    "list_latest_server_metrics",
    "list_server_metric_snapshots",
    "list_server_metrics",
    "list_servers",
    "list_sortable_tasks",
    "list_stream_profiles",
    "move_sortable_task",
    "realtime_chart_payload",
    "realtime_chart_stream",
    "run_storage_api_demo",
]
