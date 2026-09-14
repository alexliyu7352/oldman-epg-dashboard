"""Executable one-time page Message and browser Feedback examples."""

from __future__ import annotations

from markupsafe import escape
from sanic.exceptions import BadRequest
from sqlalchemy import select

from apps.auth.decorators import admin_required
from apps.examples.models import ExampleProject
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.web.api import ApiErrorCode, DefaultApiResponse, FeedbackAction, FeedbackMode, ReplaceHtmlAction
from oldman.web.messages import MessageFormat, MessageLevel, add_message, error, info, success, warning
from oldman.web.request import Request
from oldman.web.response import api_response, redirect_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_MESSAGE_PAGES = frozenset({"page", "feedback"})

app = get_app()


@app.get("/examples/messages/<page:str>", name="example_messages_page")
@add_csrf_token()
@admin_required()
async def example_messages_page(request: Request, page: str):
    """Render one concrete Message or Feedback example page."""
    if page not in OWNED_MESSAGE_PAGES:
        return await _render_example(request, "messages", page)
    context = _page_context(page)
    if page == "feedback":
        async with db_manager.get_read_session() as session:
            context["projects"] = (await session.execute(
                select(ExampleProject.id, ExampleProject.name).order_by(ExampleProject.id)
            )).all()
    return await render_template(
        f"pages/examples/messages/{page}.html",
        context=context,
    )


@app.post("/examples/messages/page/single", name="example_message_single")
@csrf_protect()
@admin_required()
async def example_message_single(request: Request):
    """Store one user-supplied message as escaped text, then redirect."""
    form_data = request.form
    if form_data is None:
        raise BadRequest("form data is required")
    content = form_data.get("content")
    raw_level = form_data.get("level")
    if not isinstance(content, str) or not content.strip():
        raise BadRequest("content must be a non-empty string")
    try:
        level = MessageLevel(raw_level)
    except (TypeError, ValueError) as exc:
        raise BadRequest("level must be success, info, warning, or error") from exc
    add_message(request, level, content.strip())
    return redirect_response("/examples/messages/page", status=303)


@app.post("/examples/messages/page/multiple", name="example_message_multiple")
@csrf_protect()
@admin_required()
async def example_message_multiple(request: Request):
    """Store all four levels in insertion order, then redirect."""
    success(request, str(_("The database changes were saved.")))
    info(request, str(_("The background import is still running.")))
    warning(request, str(_("Two optional records were skipped.")))
    error(request, str(_("One required record could not be imported.")))
    return redirect_response("/examples/messages/page", status=303)


@app.post("/examples/messages/page/trusted-html", name="example_message_trusted_html")
@csrf_protect()
@admin_required()
async def example_message_trusted_html(request: Request):
    """Store one fixed server-authored HTML message, never browser input."""
    content = "<strong>{}</strong> {}".format(
        escape(str(_("Trusted server HTML"))),
        escape(str(_("Only fixed server markup uses this mode."))),
    )
    add_message(
        request,
        MessageLevel.INFO,
        content,
        format=MessageFormat.HTML,
    )
    return redirect_response("/examples/messages/page", status=303)


@app.get("/examples/messages/feedback/default", name="example_feedback_default")
@admin_required()
async def example_feedback_default(request: Request):
    """Resolve one Feedback Action through the Page's default Feedback."""
    del request
    return api_response(
        DefaultApiResponse(
            actions=[FeedbackAction(title=_("Default Feedback Action completed."), icon="success")]
        )
    )


@app.get("/examples/messages/feedback/target", name="example_feedback_target")
@admin_required()
async def example_feedback_target(request: Request):
    """Resolve one alert through an explicitly targeted Feedback component."""
    del request
    return api_response(
        DefaultApiResponse(
            actions=[
                FeedbackAction(
                    target="#example-target-feedback",
                    mode=FeedbackMode.ALERT,
                    title=_("Explicit Feedback target completed."),
                    icon="info",
                )
            ]
        )
    )


@app.get("/examples/messages/feedback/message", name="example_feedback_message")
@admin_required()
async def example_feedback_message(request: Request):
    """Use the response message as Feedback when no Feedback Action exists."""
    del request
    return api_response(DefaultApiResponse(message=_("Response message used the Feedback fallback.")))


@app.get("/examples/messages/feedback/no-duplicate", name="example_feedback_no_duplicate")
@admin_required()
async def example_feedback_no_duplicate(request: Request):
    """Prove an explicit Feedback Action suppresses the message fallback."""
    del request
    return api_response(
        DefaultApiResponse(
            message=_("This fallback message must stay hidden."),
            actions=[
                FeedbackAction(
                    mode=FeedbackMode.ALERT,
                    title=_("Only the explicit Feedback Action is shown."),
                    icon="success",
                )
            ],
        )
    )


@app.get("/examples/messages/feedback/error", name="example_feedback_error")
@admin_required()
async def example_feedback_error(request: Request):
    """Render a business error message through the default Feedback alert."""
    del request
    return api_response(
        DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=_("The example business operation was rejected."),
        )
    )


@app.post("/examples/messages/feedback/project", name="example_feedback_project")
@csrf_protect()
@admin_required()
async def example_feedback_project(request: Request):
    """Save only the operation explicitly confirmed in the browser dialog."""
    data = request.form or {}
    operation = data.get("operation")
    if operation not in {"review", "rename"}:
        return api_response(DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=_("Choose a project operation."),
        ))
    raw_id = data.get("project_id")
    try:
        project_id = int(raw_id if isinstance(raw_id, str) else "")
    except ValueError:
        return api_response(DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=_("Choose a project."),
        ))
    raw_name = data.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if operation == "rename" and not 1 <= len(name) <= 150:
        return api_response(DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=_("Enter a project name between 1 and 150 characters."),
        ))

    async with db_manager.get_session() as session:
        project = await session.get(ExampleProject, project_id)
        if project is None:
            return api_response(DefaultApiResponse(
                error_code=ApiErrorCode.INVALID_REQUEST,
                message=_("This project no longer exists. Refresh the page."),
            ))
        if operation == "review":
            if project.status == "review":
                return api_response(DefaultApiResponse(
                    error_code=ApiErrorCode.INVALID_REQUEST,
                    message=_("This project is already in review. No changes were saved."),
                ))
            project.status = "review"
        else:
            project.name = name
        result = {"id": project.id, "name": project.name, "status": project.status}

    # Leave the transaction before reporting success; a failed commit must not look saved.
    template = request.app.ext.environment.get_template("pages/examples/messages/_project_result.html")
    html = await template.render_async(project=result)
    return api_response(DefaultApiResponse(data=result, actions=[
        ReplaceHtmlAction(html=html),
        FeedbackAction(title=_("Project changes were saved."), icon="success"),
    ]))


def _page_context(page: str) -> dict[str, object]:
    """Build the shared examples shell context for one Message page."""
    section = EXAMPLE_SECTIONS["messages"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_messages_{page}",
        "active_section": "examples_messages",
        "example_category": "messages",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["OWNED_MESSAGE_PAGES", "example_messages_page"]
