"""Database-backed Table definitions used by the Dashboard examples."""

from __future__ import annotations

from decimal import Decimal

from markupsafe import Markup, escape
from oldman.i18n import gettext_lazy as _
from oldman.web.auth import has_perm
from oldman.web.authentication import request_user
from oldman.web.components.tables import Column, SQLAlchemyTableView, TailwindTableRenderer, badge, date_cell
from oldman.web.components.tables.views import TableValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .models import ExampleProject, ExampleTeam
from .permissions import ExamplePermissions

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
    export_formats = ("csv",)
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

    async def check_permission(self, request, *, method_name: str, route_kwargs: dict[str, object]) -> tuple[bool, str | None]:
        """Signing in is not enough here: the data needs a role granting examples.view_projects.

        A plain permission needs no parameters and no query, so it is checked before either.
        """
        del method_name, route_kwargs
        return await has_perm(request, ExamplePermissions.view_projects), None

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
        return badge(row.status.title(), "success" if row.status == "active" else "secondary"), row.status

    def get_column_priority_data(self, row: ExampleProject, **kwargs: object):
        """Render project priority as a compact badge."""
        tone = "danger" if row.priority in {"high", "critical"} else "info"
        return badge(row.priority.title(), tone), row.priority

    def get_column_progress_data(self, row: ExampleProject, **kwargs: object):
        """Render progress without a client-only value source."""
        return f"{row.progress}%", row.progress

    def get_column_budget_data(self, row: ExampleProject, **kwargs: object):
        """Keep a numeric raw value while formatting the visible currency."""
        budget = Decimal(row.budget)
        return f"${budget:,.2f}", str(budget)

    def get_column_updated_at_data(self, row: ExampleProject, **kwargs: object):
        """Return stable ISO data alongside a readable timestamp."""
        return date_cell(row.updated_at)

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


class TeamProjectTable(ExampleProjectTable):
    """One team's projects, read-only: how a data endpoint decides who gets what, one hook per question.

    The Access Guards example page mounts it. Each hook answers at its own moment:

    - ``check_permission`` — before the parameters are parsed or a session is opened: staff only. It
      replaces the parent's role check; a subclass states its own rule.
    - ``check_auth`` — after the parameters, with the read session open: the team in the path must
      exist and be active, which only the database knows. It can refuse the whole request, nothing less.
    - ``apply_base_filters`` — which rows: this team's projects. It refuses nothing; totals, pages and
      search all count within it.
    """

    route_name = "example_team_projects_table"
    route_path = "/examples/auth/teams/<team_id:int>/projects/table"
    selectable = False
    export_formats = ()
    empty_message = _("This team has no projects.")
    # Read-only: no team column (it is the one in the path) and no edit or delete buttons.
    columns = tuple(
        column for column in ExampleProjectTable.columns if isinstance(column, Column) and column.name not in {"team", "action"}
    )

    async def check_permission(self, request, *, method_name: str, route_kwargs: dict[str, object]) -> tuple[bool, str | None]:
        """Staff only, decided from the signed-in user alone."""
        del method_name, route_kwargs
        return request_user(request).is_staff, None

    async def check_auth(self, table_request) -> bool:
        """The team in the path must exist and be active."""
        team = await self.require_db_session().get(ExampleTeam, table_request.route_kwargs["team_id"])
        return team is not None and team.is_active

    async def apply_base_filters(self, query, table_request):
        """Only this team's projects."""
        return query.where(ExampleProject.team_id == table_request.route_kwargs["team_id"])


__all__ = ["ExampleProjectTable", "PROJECT_PRIORITIES", "PROJECT_STATUSES", "TeamProjectTable"]
