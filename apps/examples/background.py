"""Bounded CLI demonstration of real, process-local asynchronous sampling."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
from sqlalchemy import func, select

from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext, gettext_lazy as _
from oldman.tasks import BackgroundTaskManager, TaskType

from .models import ExampleProject, ExampleTeam


async def sample_project_counts(
    counts: list[int], ready: asyncio.Event, cleaned: asyncio.Event, team_id: int | None,
) -> None:
    """Read fresh data periodically; cancellation finishes the current read context."""
    try:
        while True:
            async with db_manager.get_read_session() as session:
                statement = select(func.count()).select_from(ExampleProject)
                if team_id is not None:
                    if await session.get(ExampleTeam, team_id) is None:
                        raise ValueError(gettext("Team %(team_id)s does not exist.", team_id=team_id))
                    statement = statement.where(ExampleProject.team_id == team_id)
                counts.append(int(await session.scalar(statement) or 0))
            if len(counts) >= 2:
                ready.set()
            await asyncio.sleep(1)
    finally:
        # Also wake the owner on failure: a missing sample is never fake success.
        cleaned.set()
        ready.set()


class BackgroundStats(Command):
    """Own the local singleton for one command, never inside a live Web request."""

    name = "background-stats"
    help = _("Sample project counts twice, then cancel and clean up a local background task.")

    async def handle(
        self, team_id: Annotated[int | None, typer.Option(min=1)] = None,
    ) -> None:
        """Observe real results and both states before releasing all owned resources."""
        manager = BackgroundTaskManager()
        counts: list[int] = []
        ready, cleaned = asyncio.Event(), asyncio.Event()
        name = "example-project-count"
        manager.add_task(name, sample_project_counts, counts, ready, cleaned, team_id,
                         task_type=TaskType.PERSISTENT)
        try:
            await manager.start_all()
            async with asyncio.timeout(10):
                await ready.wait()
            task = manager.tasks[name].task
            if task is None:
                raise RuntimeError("Project sampling did not start")
            if task.done():
                await task  # Propagate its actual database/business error to the CLI.
            running = manager.get_task_status(name)
            await manager.stop_task(name)
            typer.echo(json.dumps({
                "samples": counts,
                "running": running,
                "stopped": manager.get_task_status(name),
                "cleanup_completed": cleaned.is_set(),
            }))
        finally:
            try:
                await manager.stop_all()
                await manager.remove_task(name)
            finally:
                await db_manager.close()
