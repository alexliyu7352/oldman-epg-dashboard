"""Controls for the backend HTTP example, open to every signed-in user."""

from __future__ import annotations

from oldman.web import NotFound, router
from oldman.web.auth import login_required
from oldman.web.request import Request
from oldman.web.response import replace_html_response
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_fragment, render_template

from apps.examples.apps import app as examples_app
from apps.examples.http_example import OPERATION_PATHS, REQUEST_TIMEOUT, run_http_example

from . import EXAMPLE_SECTIONS


@router.get("/examples/http/client", name="example_http_page")
@add_csrf_token()
@login_required()
async def example_http_page(request: Request):
    """Show the configured upstream without sending a network request."""
    section = EXAMPLE_SECTIONS["http"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return await render_template(
        "pages/examples/http/client.html",
        context={
            "active_page": "examples_http_client",
            "active_section": "examples_http",
            "example_category": "http",
            "example_page": "client",
            "example_page_title": pages["client"],
            "example_section": section,
            "page_entry": "examples",
            "http_base_url": str(examples_app.settings.http_base_url),
            "request_timeout": REQUEST_TIMEOUT,
        },
    )


@router.post("/examples/http/client/<operation:str>", name="example_http_operation")
@csrf_protect()
@login_required()
async def example_http_operation(request: Request, operation: str):
    """Deliver diagnostics, without confusing upstream errors with Demo errors."""
    if operation not in OPERATION_PATHS:
        raise NotFound("HTTP example operation was not found")
    result = await run_http_example(operation)
    html = await render_fragment(request, "pages/examples/http/_result.html", result=result, request_timeout=REQUEST_TIMEOUT)
    return replace_html_response(html)
