"""Model cache example: one project's tasks read through ExampleTask's cache, and the writes that invalidate them.

Every function opens its own framework session, the way a view would. The writes mark the
titles and names they touch so ``restore()`` can find them again; it writes through the ORM,
so restoring invalidates like any other write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, fields
from typing import Literal, cast

from oldman.db import db_manager
from oldman.db.sqlalchemy.cache import CacheStats
from sqlalchemy import Select, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.examples.models import ExampleProject, ExampleTask

ORM_MARK = " (ORM)"
SQL_MARK = " (SQL)"
PROJECT_MARK = " (edited)"
MARKS = (ORM_MARK, SQL_MARK, PROJECT_MARK)

Source = Literal["hits", "misses", "bypassed", "fallbacks"]


@dataclass(frozen=True)
class TaskRead:
    """One cached read of a project's tasks and how the cache served it."""

    tasks: list[ExampleTask]
    #: Which counter this read advanced; None if another request's read landed in between.
    source: Source | None
    elapsed_ms: float
    stats: CacheStats


@dataclass(frozen=True)
class Written:
    """What a write changed, for the result panel."""

    project_id: int
    project_name: str
    before: str
    after: str


def tasks_of(project_id: int) -> Select[tuple[ExampleTask]]:
    """The cached statement: it fixes ``project_id``, so only writes to this project's tasks invalidate it."""
    return select(ExampleTask).where(ExampleTask.project_id == project_id).order_by(ExampleTask.position, ExampleTask.id)


def _unmarked(text: str) -> str:
    while text.endswith(MARKS):
        text = next(text.removesuffix(mark) for mark in MARKS if text.endswith(mark))
    return text


def _toggled(text: str, mark: str) -> str:
    """Add ``mark`` to the clean text, or take it away again when it is already there."""
    return _unmarked(text) if text.endswith(mark) else _unmarked(text) + mark


async def projects_with_tasks(limit: int = 50) -> list[tuple[int, str]]:
    """Choices for the page: projects that have tasks, read without the cache."""
    has_tasks = exists().where(ExampleTask.project_id == ExampleProject.id)
    async with db_manager.get_read_session() as session:
        rows = await session.execute(select(ExampleProject.id, ExampleProject.name).where(has_tasks).order_by(ExampleProject.id).limit(limit))
        return [(project_id, name) for project_id, name in rows]


async def project_exists(project_id: int) -> bool:
    async with db_manager.get_read_session() as session:
        return await session.get(ExampleProject, project_id) is not None


async def read_project_tasks(project_id: int) -> TaskRead:
    """Read through the cache, noting which of the model's counters the read advanced."""
    manager = ExampleTask.get_cache_manager()
    async with db_manager.get_read_session() as session:
        before = manager.stats
        started = time.perf_counter()
        tasks = cast(list[ExampleTask], await manager.execute_query(session, tasks_of(project_id)))
        elapsed_ms = (time.perf_counter() - started) * 1000
        after = manager.stats
    advanced = [field.name for field in fields(CacheStats) if getattr(after, field.name) > getattr(before, field.name)]
    source = cast(Source, advanced[0]) if len(advanced) == 1 else None
    return TaskRead(tasks=tasks, source=source, elapsed_ms=elapsed_ms, stats=after)


async def _first_task(session: AsyncSession, project_id: int) -> ExampleTask | None:
    return await session.scalar(tasks_of(project_id).limit(1))


async def rename_first_task(project_id: int) -> Written | None:
    """An ordinary ORM write: the commit invalidates this project's cached task queries."""
    async with db_manager.get_session() as session:
        task = await _first_task(session, project_id)
        project = await session.get(ExampleProject, project_id)
        if task is None or project is None:
            return None
        before = task.title
        task.title = _toggled(before, ORM_MARK)
        return Written(project_id, project.name, before, task.title)


async def rename_task_in_another_project(project_id: int) -> Written | None:
    """An ORM write to a different project's task: this project's cached query stays a hit."""
    has_tasks = exists().where(ExampleTask.project_id == ExampleProject.id)
    async with db_manager.get_session() as session:
        other = await session.scalar(select(ExampleProject).where(has_tasks, ExampleProject.id != project_id).order_by(ExampleProject.id).limit(1))
        task = None if other is None else await _first_task(session, other.id)
        if other is None or task is None:
            return None
        before = task.title
        task.title = _toggled(before, ORM_MARK)
        return Written(other.id, other.name, before, task.title)


async def rename_first_task_with_sql(project_id: int) -> Written | None:
    """A bulk UPDATE with no ``invalidate_cache_on_commit``: the cache cannot see it and keeps the old title."""
    async with db_manager.get_session() as session:
        row = (
            await session.execute(
                select(ExampleTask.id, ExampleTask.title)
                .where(ExampleTask.project_id == project_id)
                .order_by(ExampleTask.position, ExampleTask.id)
                .limit(1)
            )
        ).first()
        project = await session.get(ExampleProject, project_id)
        if row is None or project is None:
            return None
        task_id, before = row
        after = _toggled(before, SQL_MARK)
        await session.execute(update(ExampleTask).where(ExampleTask.id == task_id).values(title=after))
        return Written(project_id, project.name, before, after)


async def rename_project(project_id: int) -> Written | None:
    """An ORM write to the project: ``invalidate_on`` makes every cached task query of every project miss once."""
    async with db_manager.get_session() as session:
        project = await session.get(ExampleProject, project_id)
        if project is None:
            return None
        before = project.name
        project.name = _toggled(before, PROJECT_MARK)
        return Written(project_id, project.name, before, project.name)


async def invalidate_tasks() -> None:
    """What a change made outside the application calls for: every cached task entry misses once."""
    await ExampleTask.invalidate_cache()


async def restore() -> int:
    """Take every mark this example added off again, through the ORM; returns how many rows changed."""
    async with db_manager.get_session() as session:
        tasks = (await session.scalars(select(ExampleTask).where(or_(*(ExampleTask.title.endswith(mark) for mark in MARKS))))).all()
        projects = (await session.scalars(select(ExampleProject).where(ExampleProject.name.endswith(PROJECT_MARK)))).all()
        for task in tasks:
            task.title = _unmarked(task.title)
        for project in projects:
            project.name = _unmarked(project.name)
        return len(tasks) + len(projects)
