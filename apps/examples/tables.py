"""Database-backed Table definitions used by the Dashboard examples."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from oldman.i18n import gettext_lazy as _
from oldman.web.components.tables import Column, SQLAlchemyTableView, TailwindTableRenderer
from oldman.web.components.tables.views import TableValidationError

from .models import ExampleProject

PROJECT_STATUSES = frozenset({"planned", "active", "review", "paused", "completed"})
PROJECT_PRIORITIES = frozenset({"low", "normal", "high", "critical"})


class ExampleProjectTable(SQLAlchemyTableView):
    """Show the same ExampleProject query through HTML and JSON renderers."""

    renderer_class = TailwindTableRenderer
    route_name = "example_projects_table"
    route_path = "/examples/tables/projects/table"
    model = ExampleProject
    page_size = 10
    selectable = True
    ordering = ("-updated_at",)
    search_fields = ("name", "slug", "description", "team.name")
    unsortable_columns = ("action",)
    empty_message = _("No example projects match these filters.")
    columns = (
        Column("id", _("ID")),
        Column("name", _("Project"), callback="get_column_name_data"),
        Column("team", _("Team"), field_path="team.name", callback="get_column_team_data"),
        Column("status", _("Status"), callback="get_column_status_data"),
        Column("priority", _("Priority"), callback="get_column_priority_data"),
        Column("progress", _("Progress"), callback="get_column_progress_data", type="number"),
        Column("budget", _("Budget"), callback="get_column_budget_data", type="number"),
        Column("updated_at", _("Updated"), callback="get_column_updated_at_data", type="date"),
        Column("action", _("Actions"), field_path=None, callback="get_column_action_data", exportable=False),
    )

    async def check_auth(self, request: Any) -> bool:
        """Require the same authenticated Dashboard session as the owning page."""
        session = getattr(getattr(request, "ctx", None), "session", None)
        return bool(session and session.is_authenticated())

    async def get_queryset(self):
        """Return projects with the team needed by both render paths."""
        return select(ExampleProject).options(selectinload(ExampleProject.team))

    async def filter_status(self, query, value: object, table_request):
        """Filter by a known project status."""
        status = str(value)
        if status not in PROJECT_STATUSES:
            raise TableValidationError("Invalid project status filter")
        return query.where(ExampleProject.status == status)

    async def filter_priority(self, query, value: object, table_request):
        """Filter by a known project priority."""
        priority = str(value)
        if priority not in PROJECT_PRIORITIES:
            raise TableValidationError("Invalid project priority filter")
        return query.where(ExampleProject.priority == priority)

    async def filter_team_id(self, query, value: object, table_request):
        """Filter by a numeric ExampleTeam primary key."""
        try:
            team_id = int(str(value))
        except ValueError:
            raise TableValidationError("Invalid project team filter") from None
        return query.where(ExampleProject.team_id == team_id)

    async def filter_is_active(self, query, value: object, table_request):
        """Filter enabled and archived projects without accepting arbitrary text."""
        normalized = str(value).lower()
        if normalized not in {"true", "false"}:
            raise TableValidationError("Invalid project active filter")
        return query.where(ExampleProject.is_active.is_(normalized == "true"))

    def get_column_name_data(self, row: ExampleProject, **kwargs: object):
        """Render the project name and stable slug."""
        return (
            Markup(
                f'<div class="font-medium text-default-900">{escape(row.name)}</div>'
                f'<code class="text-xs text-default-500">{escape(row.slug)}</code>'
            ),
            row.name,
        )

    def get_column_team_data(self, row: ExampleProject, **kwargs: object):
        """Render the eagerly loaded team name."""
        team_name = row.team.name if row.team is not None else str(row.team_id)
        return team_name, team_name

    def get_column_status_data(self, row: ExampleProject, **kwargs: object):
        """Render project status as a compact badge."""
        return _badge(row.status, "success" if row.status == "active" else "secondary"), row.status

    def get_column_priority_data(self, row: ExampleProject, **kwargs: object):
        """Render project priority as a compact badge."""
        tone = "danger" if row.priority in {"high", "critical"} else "info"
        return _badge(row.priority, tone), row.priority

    def get_column_progress_data(self, row: ExampleProject, **kwargs: object):
        """Render progress without a client-only value source."""
        return f"{row.progress}%", row.progress

    def get_column_budget_data(self, row: ExampleProject, **kwargs: object):
        """Keep a numeric raw value while formatting the visible currency."""
        budget = Decimal(row.budget)
        return f"${budget:,.2f}", str(budget)

    def get_column_updated_at_data(self, row: ExampleProject, **kwargs: object):
        """Return stable ISO data alongside a readable timestamp."""
        return row.updated_at.strftime("%Y-%m-%d %H:%M"), row.updated_at.isoformat()

    def get_column_action_data(self, row: ExampleProject, **kwargs: object):
        """Open the shared remote Modal for edit or delete."""
        return (
            Markup(
                '<div class="flex items-center gap-2">'
                '<button type="button" class="om-button om-button-soft-primary om-button-sm" '
                'data-om-modal-target="#example-project-modal" '
                f'data-om-modal-url="/examples/tables/projects/{row.id}/edit-modal">{escape(_("Edit"))}</button>'
                '<button type="button" class="om-button om-button-soft-danger om-button-sm" '
                'data-om-modal-target="#example-project-modal" '
                f'data-om-modal-url="/examples/tables/projects/{row.id}/delete-modal">{escape(_("Delete"))}</button>'
                "</div>"
            ),
            "",
        )


def _badge(value: str, tone: str) -> Markup:
    """Render one trusted badge around escaped database text."""
    return Markup(f'<span class="om-badge om-badge-{tone}">{escape(value.title())}</span>')


__all__ = ["ExampleProjectTable", "PROJECT_PRIORITIES", "PROJECT_STATUSES"]
