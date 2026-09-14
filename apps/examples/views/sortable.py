"""Database-backed drag and keyboard review workflow."""

from __future__ import annotations

from apps.auth.decorators import admin_required
from apps.examples import services
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.web.api import ApiErrorCode, DefaultApiResponse, FeedbackAction, HtmlSwap, ReplaceHtmlAction
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_SORTABLE_PAGES = frozenset({"workflow"})
STATUS_LABELS = {
    "todo": _("To do"),
    "in_progress": _("In progress"),
    "review": _("Review"),
    "done": _("Done"),
}
MOVE_ERRORS = {
    "critical_done": _("Critical tasks must be approved before they can move to Done."),
    "invalid_status": _("The requested task lane is invalid."),
    "not_found": _("The task is no longer available on this example board."),
    "stale_source": _("The task changed elsewhere. Refresh the board and try again."),
}

app = get_app()


@app.get("/examples/sortable/<page:str>", name="example_sortable_page")
@admin_required()
async def example_sortable_page(request: Request, page: str):
    """Render the real task board or delegate an unknown future page."""
    if page not in OWNED_SORTABLE_PAGES:
        return await _render_example(request, "sortable", page)
    return await render_template(
        "pages/examples/sortable/workflow.html",
        context=await _page_context(page),
    )


@app.post("/examples/sortable/tasks/move", name="example_sortable_move")
@csrf_protect()
@admin_required()
async def example_sortable_move(request: Request):
    """Apply one drag or keyboard move through the same domain service."""
    try:
        item_id = int(_parameter(request, "item_id"))
        source_status = _parameter(request, "source_list")
        target_status = _parameter(request, "target_list")
        target_position = int(_parameter(request, "target_position"))
    except (TypeError, ValueError):
        return _move_error("invalid_status")

    try:
        async with db_manager.get_session() as session:
            await services.move_sortable_task(
                session,
                item_id=item_id,
                source_status=source_status,
                target_status=target_status,
                target_position=target_position,
            )
    except services.SortableTaskMoveRejected as error:
        return _move_error(error.reason)

    # Dragging moves the card DOM, but counts and button URLs also need fresh state.
    return api_response(DefaultApiResponse(
        message=_("Task order saved."),
        actions=[
            ReplaceHtmlAction(
                target="#sortable-workflow-board",
                html=await _render_board(request),
                swap=HtmlSwap.OUTER,
            )
        ],
    ))


def _move_error(reason: str):
    """Return one structured business rejection so the browser can roll back."""
    return api_response(
        DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=MOVE_ERRORS[reason],
            actions=[FeedbackAction(title=MOVE_ERRORS[reason], icon="error")],
        )
    )


def _parameter(request: Request, name: str, *, required: bool = True) -> str:
    """Read one move value from drag FormData or a keyboard action URL."""
    value = request.form.get(name)
    if value is None:
        value = request.args.get(name)
    if value is None:
        if required:
            raise ValueError(f"Missing {name}")
        return ""
    return str(value)


async def _render_board(request: Request) -> str:
    """Render current counts and controls after either a drag or button move."""
    template = request.app.ext.environment.get_template("partials/examples/sortable/board.html")
    return await template.render_async(**await _board_context())


async def _board_context() -> dict[str, object]:
    """Build the shared lane context for full pages and replacement fragments."""
    return {
        "status_labels": STATUS_LABELS,
        "task_statuses": services.SORTABLE_TASK_STATUSES,
        "tasks_by_status": await services.list_sortable_tasks(),
    }


async def _page_context(page: str) -> dict[str, object]:
    """Build the Sortable page shell and real board data."""
    section = EXAMPLE_SECTIONS["sortable"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_sortable_{page}",
        "active_section": "examples_sortable",
        "example_category": "sortable",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
        **await _board_context(),
    }


__all__ = ["OWNED_SORTABLE_PAGES", "example_sortable_move", "example_sortable_page"]
