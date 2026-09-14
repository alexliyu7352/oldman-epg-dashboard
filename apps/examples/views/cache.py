"""Protected controls for observing real Redis cache hits and invalidation."""

from __future__ import annotations

from apps.auth.decorators import admin_required
from apps.examples.cache_example import CACHE_TTL, calculate_project_statistics, clear_project_statistics, read_project_statistics
from oldman.cache import cache_response
from oldman.web import NotFound
from oldman.web.request import Request
from oldman.web.response import replace_html_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

app = get_app()
RESPONSE_PREFIX = "oldman_epg_dashboard:examples:response-statistics"
RESPONSE_TTL = 5


@app.get("/examples/cache/redis", name="example_cache_page")
@add_csrf_token()
@admin_required()
async def example_cache_page(request: Request):
    """Render the controls without reading project data or warming the cache."""
    section = EXAMPLE_SECTIONS["cache"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return await render_template(
        "pages/examples/cache/redis.html",
        context={
            "active_page": "examples_cache_redis",
            "active_section": "examples_cache",
            "example_category": "cache",
            "example_page": "redis",
            "example_page_title": pages["redis"],
            "example_section": section,
            "page_entry": "examples",
            "cache_ttl": CACHE_TTL,
            "response_cache_ttl": RESPONSE_TTL,
        },
    )


@app.post("/examples/cache/redis/<operation:str>", name="example_cache_operation")
@csrf_protect()
@admin_required()
async def example_cache_operation(request: Request, operation: str):
    """Replace the result panel using the existing ordered Action protocol."""
    if operation not in {"read", "refresh", "clear"}:
        raise NotFound("Cache example operation was not found")

    context: dict[str, object]
    if operation == "clear":
        context = {"deleted": await clear_project_statistics(), "source": "cleared"}
    else:
        statistics, source = await read_project_statistics(refresh=operation == "refresh")
        context = {"statistics": statistics, "source": source}
    template = request.app.ext.environment.get_template("pages/examples/cache/_result.html")
    html = await template.render_async(**context)
    return replace_html_response(html)


@app.get("/examples/cache/response", name="example_cached_response")
@admin_required()
@cache_response(key_prefix=RESPONSE_PREFIX, expiration=RESPONSE_TTL, use_pickle=False,
                vary_by=lambda request: getattr(request.ctx, "locale", ""))
async def example_cached_response(request: Request):
    """Cache only this cookie-free, language-specific statistics response after staff checks."""
    statistics = await calculate_project_statistics()
    template = request.app.ext.environment.get_template("pages/examples/cache/_response_result.html")
    return replace_html_response(await template.render_async(statistics=statistics))


@app.post("/examples/cache/response", name="example_invalidate_response")
@csrf_protect()
@admin_required()
@cache_response(key_prefix=RESPONSE_PREFIX, use_pickle=False)
async def example_invalidate_response(request: Request):
    """The decorator invalidates every cached query/language variant after this returns."""
    template = request.app.ext.environment.get_template("pages/examples/cache/_response_result.html")
    return replace_html_response(await template.render_async(cleared=True))
