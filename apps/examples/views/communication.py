"""Staff-only Core communication using ordinary Forms and response actions."""

from __future__ import annotations

import logging

from nats.errors import ConnectionClosedError, NoRespondersError, OutboundBufferLimitError
from sqlalchemy import select

from apps.auth.decorators import admin_required
from apps.examples.forms import CommunicationProjectForm
from apps.examples.models import ExampleProject
from apps.examples.nats_example import PEERS, query_and_publish_status, query_observations, query_project_status, send_example_events
from apps.examples.nats_messages import ExampleEvent
from oldman.conf import settings
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.providers.nats import bus
from oldman.web import NotFound
from oldman.web.api import ApiErrorCode, DefaultApiFormResponse, ReplaceHtmlAction
from oldman.web.components.forms import TailwindForm
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

app = get_app()
logger = logging.getLogger(__name__)
OPERATIONS = {"query", "publish", "compete", "broadcast", "observe", "missing", "slow", "raise"}
COMMUNICATION_ERRORS = (NoRespondersError, TimeoutError, ConnectionClosedError, OutboundBufferLimitError)


def communication_error(error: BaseException):
    """Describe known transport outcomes, never reconstruct remote exceptions."""
    if isinstance(error, NoRespondersError):
        return _("No receiver is listening at this address. Check the selected service.")
    if isinstance(error, TimeoutError):
        return _("The reply timed out. The receiver may still be running; check its log. This request was not retried.")
    return _("The message could not be sent: the NATS connection is closed or its reconnect buffer is full.")


async def project_choices() -> list[tuple[int, str]]:
    """Load current choices without sending any RPC or creating sample data."""
    async with db_manager.get_read_session() as session:
        rows = (await session.execute(select(ExampleProject.id, ExampleProject.name).order_by(ExampleProject.name))).all()
        return [(row.id, row.name) for row in rows]


@app.get("/examples/communication/<page:str>", name="example_communication_page")
@add_csrf_token()
@admin_required()
async def example_communication_page(request: Request, page: str):
    """Opening a page reads only its choices/configuration, never contacts peers."""
    if page not in {"rpc", "events", "failures"}:
        raise NotFound("Communication example page was not found")
    section = EXAMPLE_SECTIONS["communication"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    choices = await project_choices() if page == "rpc" else []
    forms = {}
    for operation in ("query", "publish"):
        form = CommunicationProjectForm(request=request, prefix=operation)
        form.project_id.choices = [(0, str(_("Choose a record"))), *choices]
        forms[operation] = form
    return await render_template(f"pages/examples/communication/{page}.html", context={
        "active_page": f"examples_communication_{page}", "active_section": "examples_communication",
        "example_category": "communication", "example_page": page, "example_page_title": pages[page],
        "example_section": section, "page_entry": "examples", "bus_enabled": settings.nats_bus.enabled,
        "projects": choices, "rpc_forms": forms, "operation_form": TailwindForm(request=request),
    })


async def communication_result(request: Request, *, error=None, **context):
    """Keep all response rendering in one place, including partial node results."""
    template = request.app.ext.environment.get_template("pages/examples/communication/_result.html")
    html = await template.render_async(error=error, **context)
    return api_response(DefaultApiFormResponse(
        error_code=ApiErrorCode.INVALID_REQUEST if error else ApiErrorCode.OK,
        message=error or "", actions=[ReplaceHtmlAction(html=html, target="#communication-result")],
    ))


@app.post("/examples/communication/run/<operation:str>", name="example_communication_run")
@csrf_protect()
@admin_required()
async def example_communication_run(request: Request, operation: str):
    """Only fixed operations are accepted; shared HTTP/Form loading needs no patch."""
    if operation not in OPERATIONS:
        raise NotFound("Communication example operation was not found")
    if not settings.nats_bus.enabled:
        return await communication_result(request, error=_("Enable nats_bus in this service before using the example."))

    if operation == "observe":
        rows = []
        for peer in PEERS:
            try:
                rows.append({"peer_id": peer, "snapshot": await query_observations(peer)})
            except COMMUNICATION_ERRORS as error:
                logger.warning("Demo observation failed for %s: %r", peer, error)
                rows.append({"peer_id": peer, "error": communication_error(error)})
        partial_error = _("One or more receiving services could not be read.") if any("error" in row for row in rows) else None
        return await communication_result(request, error=partial_error, observations=rows)

    try:
        if operation in {"query", "publish"}:
            form = CommunicationProjectForm.from_request(request, prefix=operation)
            form.project_id.choices = [(0, str(_("Choose a record"))), *await project_choices()]
            if not await form.validate():
                return api_response(form.to_api_response())
            query = query_project_status if operation == "query" else query_and_publish_status
            reply = await query(int(form.cleaned_data["project_id"]), str(form.cleaned_data["peer_id"]))
            error = None if reply.found else _("The selected record no longer exists.")
            return await communication_result(request, error=error, reply=reply, reported=operation == "publish")
        if operation in {"compete", "broadcast"}:
            await send_example_events("compete" if operation == "compete" else "broadcast")
            return await communication_result(request, sent=10, kind=operation)
        subject, peer, timeout = {
            "missing": ("demo.failure.slow", "missing_demo_receiver", 0.5),
            "slow": ("demo.failure.slow", "monitor_a", 0.5),
            "raise": ("demo.failure.raise", "monitor_a", 0.5),
        }[operation]
        await bus.request(ExampleEvent(sequence=1), subject, ExampleEvent, peer_id=peer, request_timeout=timeout)
    except COMMUNICATION_ERRORS as error:
        logger.warning("Demo communication %s failed: %r", operation, error)
        return await communication_result(request, error=communication_error(error), operation=operation)
    return await communication_result(request, unexpected_reply=True)
