"""Concrete Dashboard UI reference pages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apps.auth.decorators import admin_required
from oldman.web.request import Request
from oldman.web.routing import get_app
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_UI_PAGES = frozenset(EXAMPLE_SECTIONS["ui"]["pages"])

app = get_app()


@app.get("/examples/ui/<page:str>", name="example_ui_page")
@admin_required()
async def example_ui_page(request: Request, page: str):
    """Render one reference page using the live Dashboard design system."""
    if page not in OWNED_UI_PAGES:
        return await _render_example(request, "ui", page)
    section = EXAMPLE_SECTIONS["ui"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    context = {
        "active_page": f"examples_ui_{page.replace('-', '_')}",
        "active_section": "examples_ui",
        "example_category": "ui",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }
    if page == "countdown":
        context["countdown_target"] = (
            datetime.now(UTC) + timedelta(days=2, hours=4, minutes=30, seconds=45)
        ).isoformat()

    return await render_template(
        f"pages/examples/ui/{page}.html",
        context=context,
    )


__all__ = ["OWNED_UI_PAGES", "example_ui_page"]
