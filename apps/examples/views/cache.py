"""Protected controls for observing real Redis cache hits and invalidation, and the model cache."""

from __future__ import annotations

from oldman.cache import cache_response
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound, router
from oldman.web.api import ApiErrorCode, ReplaceHtmlAction, form_error_response, form_response
from oldman.web.auth import staff_required
from oldman.web.request import Request
from oldman.web.response import replace_html_response
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_fragment, render_template

from apps.examples import model_cache
from apps.examples.cache_example import CACHE_TTL, calculate_project_statistics, clear_project_statistics, read_project_statistics

from . import EXAMPLE_SECTIONS

RESPONSE_PREFIX = "oldman_epg_dashboard:examples:response-statistics"
RESPONSE_TTL = 5


@router.get("/examples/cache/redis", name="example_cache_page")
@add_csrf_token()
@staff_required()
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


@router.post("/examples/cache/redis/<operation:str>", name="example_cache_operation")
@csrf_protect()
@staff_required()
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
    html = await render_fragment(request, "pages/examples/cache/_result.html", **context)
    return replace_html_response(html)


@router.get("/examples/cache/response", name="example_cached_response")
@staff_required()
@cache_response(key_prefix=RESPONSE_PREFIX, expiration=RESPONSE_TTL, use_pickle=False,
                vary_by=lambda request: getattr(request.ctx, "locale", ""))
async def example_cached_response(request: Request):
    """Cache only this cookie-free, language-specific statistics response after staff checks."""
    statistics = await calculate_project_statistics()
    return replace_html_response(await render_fragment(request, "pages/examples/cache/_response_result.html", statistics=statistics))


@router.post("/examples/cache/response", name="example_invalidate_response")
@csrf_protect()
@staff_required()
@cache_response(key_prefix=RESPONSE_PREFIX, use_pickle=False)
async def example_invalidate_response(request: Request):
    """The decorator invalidates every cached query/language variant after this returns."""
    return replace_html_response(await render_fragment(request, "pages/examples/cache/_response_result.html", cleared=True))


MODEL_CACHE_OPERATIONS = {"read", "rename", "rename_other", "rename_sql", "rename_project", "invalidate", "restore"}


@router.get("/examples/cache/models", name="example_model_cache_page")
@add_csrf_token()
@staff_required()
async def example_model_cache_page(request: Request):
    """Render the project choices; nothing is read through the cache until an operation asks."""
    section = EXAMPLE_SECTIONS["cache"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return await render_template(
        "pages/examples/cache/models.html",
        context={
            "active_page": "examples_cache_models",
            "active_section": "examples_cache",
            "example_category": "cache",
            "example_page": "models",
            "example_page_title": pages["models"],
            "example_section": section,
            "page_entry": "examples",
            "projects": await model_cache.projects_with_tasks(),
        },
    )


@router.post("/examples/cache/models", name="example_model_cache_operation")
@csrf_protect()
@staff_required()
async def example_model_cache_operation(request: Request):
    """Run one read or write chosen by the pressed button and describe what the cache did."""
    form = request.form or {}
    operation = form.get("operation", "")
    if operation not in MODEL_CACHE_OPERATIONS:
        raise NotFound("Model cache example operation was not found")
    raw = form.get("project_id", "")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) <= 2147483647:
        message = _("Choose a project.")
        return form_error_response(message, errors={"project_id": message}, error_code=ApiErrorCode.INVALID_REQUEST)
    project_id = int(raw)
    if not await model_cache.project_exists(project_id):
        message = _("The selected project no longer exists.")
        return form_error_response(message, errors={"project_id": message}, error_code=ApiErrorCode.INVALID_REQUEST)

    context: dict[str, object] = {"operation": operation}
    if operation == "read":
        context["read"] = await model_cache.read_project_tasks(project_id)
    elif operation == "invalidate":
        await model_cache.invalidate_tasks()
    elif operation == "restore":
        context["restored"] = await model_cache.restore()
    else:
        write = {
            "rename": model_cache.rename_first_task,
            "rename_other": model_cache.rename_task_in_another_project,
            "rename_sql": model_cache.rename_first_task_with_sql,
            "rename_project": model_cache.rename_project,
        }[operation]
        context["written"] = await write(project_id)
    html = await render_fragment(request, "pages/examples/cache/_model_result.html", **context)
    return form_response(error_code=ApiErrorCode.OK, actions=[ReplaceHtmlAction(html=html, target="#model-cache-result")])
