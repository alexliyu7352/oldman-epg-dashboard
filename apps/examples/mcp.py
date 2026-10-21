"""MCP tools, a resource and a prompt over the example projects, for agents such as Claude Code.

``services/web.py`` installs them with ``install_mcp(app)``; they are offered once ``web.mcp.enabled`` is
true in ``data/web_settings.yaml`` (README, "MCP 示例"). Reading projects asks for
``examples.view_projects``, as the project pages do: only signed-in users holding it see those, over a
JWT access token. An API key named in ``web.mcp.callers`` is a program, not a user, so it has no
permissions and sees only ``example_caller``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from oldman.db import db_manager
from oldman.mcp import ToolAnnotations, ToolError, mcp
from oldman.serializers import MsgspecModel
from oldman.web import Request
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from .models import ExampleProject
from .permissions import ExamplePermissions
from .tables import PROJECT_STATUSES

_STATUSES = ", ".join(sorted(PROJECT_STATUSES))
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


class CallerInfo(MsgspecModel, kw_only=True):
    """How the request reached the MCP endpoint."""

    method: str | None
    user: str | None
    caller: str | None


class ProjectSummary(MsgspecModel, kw_only=True):
    """One example project as a list shows it."""

    id: int
    slug: str
    name: str
    team: str
    status: str
    priority: str
    progress: int


class ProjectSearch(MsgspecModel, kw_only=True):
    """The projects that matched, at most ``limit`` of ``total``."""

    total: int
    projects: list[ProjectSummary]


class ProjectDetail(MsgspecModel, kw_only=True):
    """One example project with the fields its edit window shows."""

    id: int
    slug: str
    name: str
    team: str
    status: str
    priority: str
    progress: int
    budget: str
    start_date: str | None
    end_date: str | None
    description: str | None


@mcp.tool(annotations=_READ_ONLY)
async def example_caller(request: Request) -> CallerInfo:
    """Say how this MCP request authenticated: the method, the signed-in user, or the API key's name.

    Every admitted caller sees this tool; use it to check a connection before calling the others.
    """
    auth = request.ctx.auth
    user = request.ctx.user
    return CallerInfo(
        method=auth.method if auth is not None else None,
        user=user.username if user.is_authenticated else None,
        caller=auth.caller if auth is not None else None,
    )


@mcp.tool(permission=ExamplePermissions.view_projects, annotations=_READ_ONLY)
async def find_example_projects(
    text: Annotated[str, Field(description="Part of the name or slug; empty matches every project")] = "",
    status: Annotated[str | None, Field(description=f"Only projects in this status: {_STATUSES}")] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="How many projects to return")] = 10,
) -> ProjectSearch:
    """Find example projects by name or slug, optionally in one status, ordered by id."""
    if status is not None and status not in PROJECT_STATUSES:
        raise ToolError(f"Unknown status {status!r}; use one of {_STATUSES}")
    conditions = []
    if text:
        pattern = f"%{text}%"
        conditions.append(or_(ExampleProject.name.ilike(pattern), ExampleProject.slug.ilike(pattern)))
    if status is not None:
        conditions.append(ExampleProject.status == status)
    async with db_manager.get_read_session() as session:
        total = await session.scalar(select(func.count()).select_from(ExampleProject).where(*conditions)) or 0
        rows = await session.scalars(
            select(ExampleProject).options(selectinload(ExampleProject.team)).where(*conditions).order_by(ExampleProject.id).limit(limit)
        )
        projects = [
            ProjectSummary(
                id=project.id,
                slug=project.slug,
                name=project.name,
                team=project.team.name,
                status=project.status,
                priority=project.priority,
                progress=project.progress,
            )
            for project in rows
        ]
    return ProjectSearch(total=total, projects=projects)


@mcp.tool(permission=ExamplePermissions.view_projects, annotations=_READ_ONLY)
async def count_example_projects_by_status() -> dict[str, int]:
    """Count the example projects in each status."""
    async with db_manager.get_read_session() as session:
        rows = await session.execute(select(ExampleProject.status, func.count()).group_by(ExampleProject.status))
        counts = dict(rows.tuples().all())
    return {status: counts.get(status, 0) for status in sorted(PROJECT_STATUSES)}


@mcp.resource("example-project://{slug}", permission=ExamplePermissions.view_projects)
async def example_project(slug: str) -> ProjectDetail | None:
    """One example project by its slug, e.g. example-project://example-project-001."""
    async with db_manager.get_read_session() as session:
        project = await session.scalar(select(ExampleProject).options(selectinload(ExampleProject.team)).where(ExampleProject.slug == slug))
        if project is None:
            return None
        return ProjectDetail(
            id=project.id,
            slug=project.slug,
            name=project.name,
            team=project.team.name,
            status=project.status,
            priority=project.priority,
            progress=project.progress,
            budget=str(project.budget),
            start_date=_iso(project.start_date),
            end_date=_iso(project.end_date),
            description=project.description,
        )


@mcp.prompt()
async def review_example_project(
    slug: Annotated[str, Field(description="The project's slug, e.g. example-project-001")],
) -> str:
    """Ask for a short review of one example project: progress, schedule and what to do next."""
    return (
        f"Read the example project at example-project://{slug}. Review it in a few sentences: is its progress "
        "in line with its status and dates, what stands out in its budget and priority, and what should happen "
        "next. Use find_example_projects to compare it with other projects of the same team if that helps."
    )


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None
