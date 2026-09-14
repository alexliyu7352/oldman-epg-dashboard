"""Turbo navigation, request cancellation and Loading examples."""

from __future__ import annotations

import asyncio

from apps.auth.decorators import admin_required
from oldman.i18n import gettext_lazy as _
from oldman.web.api import DefaultApiResponse, ReplaceHtmlAction
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_NAVIGATION_PAGES = frozenset({"lifecycle", "actions", "loading"})

app = get_app()


@app.get("/examples/navigation/<page:str>", name="example_navigation_page")
@admin_required()
async def example_navigation_page(request: Request, page: str):
    """Render one navigation page; the slow query exposes main-frame Loading."""
    if page not in OWNED_NAVIGATION_PAGES:
        return await _render_example(request, "navigation", page)
    if page == "loading" and request.args.get("slow") == "1":
        await asyncio.sleep(1)
    return await render_template(
        f"pages/examples/navigation/{page}.html",
        context=_page_context(page),
    )


@app.get("/examples/navigation/actions/slow", name="example_navigation_slow_action")
@admin_required()
async def example_navigation_slow_action(request: Request):
    """Return late HTML so leaving the frame can demonstrate request cancellation."""
    del request
    await asyncio.sleep(1)
    return api_response(
        DefaultApiResponse(
            actions=[ReplaceHtmlAction(html=str(_("The slow Action completed on its original page.")))]
        )
    )


@app.get("/examples/navigation/loading/wait", name="example_navigation_loading_wait")
@admin_required()
async def example_navigation_loading_wait(request: Request):
    """Complete one real delayed request used by both Loading scopes."""
    del request
    await asyncio.sleep(1)
    return api_response(DefaultApiResponse(message=_("The delayed server request completed.")))


def _page_context(page: str) -> dict[str, object]:
    """Build the shared examples shell context for one navigation page."""
    section = EXAMPLE_SECTIONS["navigation"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_navigation_{page}",
        "active_section": "examples_navigation",
        "example_category": "navigation",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["OWNED_NAVIGATION_PAGES", "example_navigation_page"]
