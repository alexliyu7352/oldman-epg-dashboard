"""Database-backed static, dynamic and realtime Table examples."""

from __future__ import annotations

import asyncio
from itertools import cycle

from apps.auth.decorators import admin_required
from apps.examples import services
from apps.examples.forms import ExampleProjectFilterForm, ExampleProjectForm
from apps.examples.models import ExampleProject
from apps.examples.tables import ExampleProjectTable
from oldman.db import db_manager
from oldman.i18n import LazyTranslation
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound
from oldman.web.api import ApiErrorCode, CloseModalAction, DefaultApiFormResponse, FeedbackAction, ReloadTableAction
from oldman.web.components.tables import TableResult
from oldman.web.request import Request
from oldman.web.response import json_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.sse import SSEQueueMode, SSEStream, sse
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_TABLE_PAGES = frozenset({"static", "responsive", "html", "json", "states", "realtime", "advanced"})
FILTER_NAMES = ("team_id", "status", "priority", "is_active")

app = get_app()
app.add_route(ExampleProjectTable.as_view(), ExampleProjectTable.route_path, name=ExampleProjectTable.route_name)


@app.get("/examples/tables/<page:str>", name="example_tables_page")
@admin_required()
async def example_tables_page(request: Request, page: str):
    """Render one concrete Table example page."""
    if page not in OWNED_TABLE_PAGES:
        return await _render_example(request, "tables", page)
    context = _page_context(page)

    if page in {"static", "responsive"}:
        context["projects"] = await services.list_projects(limit=12 if page == "static" else 8)
        return await render_template(f"pages/examples/tables/{page}.html", context=context)

    if page in {"html", "json"}:
        async with db_manager.get_read_session() as session:
            context.update(
                filter_form=ExampleProjectFilterForm.from_query(request, session=session),
                table=_project_table(request),
                table_format=page,
            )
            return await render_template("pages/examples/tables/dynamic.html", context=context)

    if page == "realtime":
        context["metric_rows"] = await services.list_latest_server_metrics()
        return await render_template("pages/examples/tables/realtime.html", context=context)

    if page == "states":
        table = ExampleProjectTable(request=request)
        table_request = table.build_table_request(request, route_kwargs={})
        empty_result = TableResult(
            rows=[],
            row_contexts=[],
            total=0,
            filtered_total=0,
            page=1,
            page_size=table.page_size,
        )
        renderer = table.get_renderer()
        context.update(
            loading_fragment=renderer.render_initial_fragment(data_format="html"),
            empty_fragment=await table.render_html_fragment(table_request, empty_result),
            error_fragment=renderer.render_error_fragment(str(_("The Table request failed."))),
            permission_fragment=renderer.render_error_fragment(str(_("Permission denied."))),
            selectable_table=table,
        )
        return await render_template("pages/examples/tables/states.html", context=context)

    context["gaps"] = (
        (_("Data export"), _("Export the current filtered dataset without loading every row into the browser.")),
        (_("Sticky headers and columns"), _("Keep identifiers visible in long and wide operational tables.")),
        (_("Column visibility"), _("Let users choose which optional columns remain visible.")),
        (_("Bulk actions"), _("Apply one validated action to selected rows.")),
    )
    return await render_template("pages/examples/tables/advanced.html", context=context)


@app.get("/examples/tables/realtime/events", name="example_realtime_table_events")
@admin_required()
@sse.streaming(queue_mode=SSEQueueMode.LATEST, session_guard=True, login_url="/login")
async def example_realtime_table_events(request: Request, stream: SSEStream) -> None:
    """Replay database-backed server samples through one page-owned SSE stream."""
    del request
    snapshots = await services.list_server_metric_snapshots()
    for samples in cycle(snapshots):
        if stream.is_closed:
            return
        # Viewing the demo replays stored samples; only explicit publishing writes data.
        await stream.send(services._realtime_payload(samples), event="examples.table.metrics")
        await asyncio.sleep(1)


@app.get("/examples/tables/projects/new-modal", name="example_project_create_modal")
@add_csrf_token()
@admin_required()
async def example_project_create_modal(request: Request):
    """Load a create Form into the shared remote Modal."""
    async with db_manager.get_read_session() as session:
        form = ExampleProjectForm(request=request, session=session)
        html = await form.render(
            action="/examples/tables/projects/create",
            form_mode="json",
            submit_label=_("Create project"),
            validate=True,
        )
    return json_response({"title": str(_("Create example project")), "html": str(html)})


@app.post("/examples/tables/projects/create", name="example_project_create")
@csrf_protect()
@admin_required()
async def example_project_create(request: Request):
    """Create one Project and reload the mounted HTML or JSON Table."""
    async with db_manager.get_session() as session:
        form = ExampleProjectForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict())
        await form.save(commit=True, session=session)
    return _project_saved_response(_("Project created."))


@app.get("/examples/tables/projects/<project_id:int>/edit-modal", name="example_project_edit_modal")
@add_csrf_token()
@admin_required()
async def example_project_edit_modal(request: Request, project_id: int):
    """Load an existing Project Form into the shared remote Modal."""
    async with db_manager.get_read_session() as session:
        project = await _project_or_404(session, project_id)
        form = ExampleProjectForm(request=request, instance=project, session=session)
        html = await form.render(
            action=f"/examples/tables/projects/{project_id}/update",
            form_mode="json",
            submit_label=_("Save project"),
            validate=True,
        )
    return json_response({"title": str(_("Edit example project")), "html": str(html)})


@app.post("/examples/tables/projects/<project_id:int>/update", name="example_project_update")
@csrf_protect()
@admin_required()
async def example_project_update(request: Request, project_id: int):
    """Update one Project through the ordinary ModelForm transaction."""
    async with db_manager.get_session() as session:
        project = await _project_or_404(session, project_id)
        form = ExampleProjectForm.from_request(request, instance=project, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict())
        await form.save(commit=True, session=session)
    return _project_saved_response(_("Project saved."))


@app.get("/examples/tables/projects/<project_id:int>/delete-modal", name="example_project_delete_modal")
@add_csrf_token()
@admin_required()
async def example_project_delete_modal(request: Request, project_id: int):
    """Render a real confirmation Form for one Project."""
    async with db_manager.get_read_session() as session:
        project = await _project_or_404(session, project_id)
        template = request.app.ext.environment.get_template("partials/examples/tables/delete_project.html")
        html = await template.render_async(project=project, csrf_token=request.ctx.csrf_token)
    return json_response({"title": str(_("Delete example project")), "html": html})


@app.post("/examples/tables/projects/<project_id:int>/delete", name="example_project_delete")
@csrf_protect()
@admin_required()
async def example_project_delete(request: Request, project_id: int):
    """Delete one Project and its fixture-owned child rows."""
    del request
    async with db_manager.get_session() as session:
        project = await _project_or_404(session, project_id)
        await session.delete(project)
    return _project_saved_response(_("Project deleted."))


def _project_table(request: Request) -> ExampleProjectTable:
    """Build the shared dynamic Table state from visible page query values."""
    return ExampleProjectTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in FILTER_NAMES},
        initial_query=request.args.get("q", "").strip(),
    )


async def _project_or_404(session, project_id: int) -> ExampleProject:
    """Return one Project in the caller's active transaction."""
    project = await session.get(ExampleProject, project_id)
    if project is None:
        raise NotFound("Example project was not found")
    return project


def _project_saved_response(message: str | LazyTranslation):
    """Close the Modal and refresh whichever render mode is mounted."""
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=message,
        actions=[
            FeedbackAction(title=message, icon="success"),
            CloseModalAction(),
            ReloadTableAction(target="#example-projects-table"),
        ],
    )
    return json_response(payload.to_dict())


def _page_context(page: str) -> dict[str, object]:
    """Build the shared examples shell context for one Table page."""
    section = EXAMPLE_SECTIONS["tables"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_tables_{page.replace('-', '_')}",
        "active_section": "examples_tables",
        "example_category": "tables",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["OWNED_TABLE_PAGES", "example_tables_page"]
