"""Read real Demo data through the installed App command lifecycle."""

from __future__ import annotations

from typing import Annotated

import typer
from sqlalchemy import func, select

from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext
from oldman.i18n import gettext_lazy as _

from .models import ExampleProject, ExampleTeam
from .background import BackgroundStats
from .process_examples import PythonProcessDemo, SubprocessDemo
from .worker_examples import WorkerDemo
from .cache_levels import CacheLevels
from .django_cache import DjangoCacheDemo
from .image_cache import ImageCacheDemo
from .cache_functions import CachedStats


class ProjectStats(Command):
    """Count projects without changing fixtures, records or cache snapshots."""

    name = "project-stats"
    help = _("Count example projects, optionally filtered by team.")

    async def handle(
        self,
        team_id: Annotated[int | None, typer.Option(min=1)] = None,
    ) -> None:
        """Read a fresh grouped count; CLI validation restricts the optional ID."""
        try:
            async with db_manager.get_read_session() as session:
                statement = (
                    select(ExampleProject.status, func.count())
                    .group_by(ExampleProject.status)
                    .order_by(ExampleProject.status)
                )
                if team_id is not None:
                    if await session.get(ExampleTeam, team_id) is None:
                        raise ValueError(gettext("Team %(team_id)s does not exist.", team_id=team_id))
                    statement = statement.where(ExampleProject.team_id == team_id)
                counts = {status: count for status, count in await session.execute(statement)}
            # Plain output keeps database values from being interpreted as Rich markup.
            typer.echo(gettext("Total projects: %(count)s", count=sum(counts.values())))
            for status, count in counts.items():
                typer.echo(f"{status}: {count}")
        finally:
            # One-shot commands do not run the Web service's shutdown listeners.
            await db_manager.close()


__all__ = ["ProjectStats", "BackgroundStats", "PythonProcessDemo", "SubprocessDemo", "WorkerDemo", "CacheLevels", "DjangoCacheDemo", "ImageCacheDemo", "CachedStats"]
