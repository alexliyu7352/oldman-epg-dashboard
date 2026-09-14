"""Database and realtime Chart examples."""

from __future__ import annotations

import asyncio

from sanic.exceptions import BadRequest

from apps.auth.decorators import admin_required
from apps.examples import services
from apps.examples.chart_views import (
    COMPOSITION_CHARTS,
    DISTRIBUTION_CHARTS,
    REALTIME_CHART,
    TREND_CHARTS,
    ExampleChartData,
)
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.web.api import ApiErrorCode, DefaultApiResponse, FeedbackAction
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.sse import SSEPublisher, SSEQueueMode, SSEStream, sse
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_CHART_PAGES = frozenset(
    {"trends", "composition", "distribution", "states", "realtime"}
)
CHART_EVENT = "examples.chart.metric"

app = get_app()
app.add_route(
    ExampleChartData.as_view(),
    ExampleChartData.route_path,
    name=ExampleChartData.route_name,
)


@app.get("/examples/charts/<page:str>", name="example_charts_page")
@add_csrf_token()
@admin_required()
async def example_charts_page(request: Request, page: str):
    """Render one concrete database-backed Chart page."""
    if page not in OWNED_CHART_PAGES:
        return await _render_example(request, "charts", page)
    context = _page_context(page)
    if page == "states":
        context["chart"] = await _chart_shell(request, "states", "example-chart-states")
        return await render_template(
            "pages/examples/charts/states.html", context=context
        )
    if page == "realtime":
        context.update(
            chart=await _chart_shell(request, REALTIME_CHART, "example-chart-realtime"),
            servers=await services.list_servers(),
        )
        return await render_template(
            "pages/examples/charts/realtime.html", context=context
        )

    keys = {
        "trends": TREND_CHARTS,
        "composition": COMPOSITION_CHARTS,
        "distribution": DISTRIBUTION_CHARTS,
    }[page]
    context["charts"] = await _chart_cards(request, keys)
    return await render_template("pages/examples/charts/gallery.html", context=context)


@app.get("/examples/charts/realtime/events", name="example_realtime_chart_events")
@admin_required()
@sse.streaming(queue_mode=SSEQueueMode.LATEST, session_guard=True, login_url="/login")
async def example_realtime_chart_events(request: Request, stream: SSEStream) -> None:
    """Replay database rows locally, then wait on the shared Redis stream."""
    server_id = _server_id(request)
    for metric in await services.list_server_metrics(server_id, limit=6):
        if stream.is_closed:
            return
        await stream.send(
            services.realtime_chart_payload(metric, source="stream"), event=CHART_EVENT
        )
        await asyncio.sleep(0.12)
    await stream.subscribe(services.realtime_chart_stream(server_id))


@app.post("/examples/charts/realtime/publish", name="example_realtime_chart_publish")
@csrf_protect()
@admin_required()
async def example_realtime_chart_publish(request: Request):
    """Commit one metric, then publish it through the configured SSE Redis channel."""
    server_id = _server_id(request)
    async with db_manager.get_session() as session:
        try:
            payload = await services.create_server_metric(session, server_id)
        except ValueError as exc:
            raise BadRequest("Unknown example server") from exc
    published = await SSEPublisher.from_settings().publish_stream(
        stream=services.realtime_chart_stream(server_id),
        event=CHART_EVENT,
        payload=payload,
    )
    if not published:
        return api_response(
            DefaultApiResponse(
                error_code=ApiErrorCode.INVALID_REQUEST,
                message=_("The metric was saved, but Redis delivery failed."),
            )
        )
    return api_response(
        DefaultApiResponse(
            message=_("A new database metric was published."),
            actions=[
                FeedbackAction(title=_("Realtime metric published."), icon="success")
            ],
        )
    )


async def _chart_cards(
    request: Request, keys: tuple[str, ...]
) -> list[dict[str, object]]:
    titles = {
        "project-trend": _("Project start trend"),
        "task-trend": _("Task due trend"),
        "project-progress": _("Progress by status"),
        "team-budget": _("Budget by team"),
        "project-status": _("Project status"),
        "task-priority": _("Task priority"),
        "project-completion": _("Average completion"),
        "project-stacked": _("Status and priority"),
        "server-scatter": _("CPU and memory"),
        "server-bubble": _("Upload and download"),
        "server-heatmap": _("Server CPU heatmap"),
        "team-treemap": _("Team budget treemap"),
    }
    cards: list[dict[str, object]] = []
    for key in keys:
        cards.append(
            {
                "key": key,
                "title": titles[key],
                "chart": await _chart_shell(request, key, f"example-chart-{key}"),
            }
        )
    return cards


async def _chart_shell(request: Request, key: str, html_id: str):
    chart = ExampleChartData(request=request)
    return await chart.render_shell(html_id=html_id, chart_key=key)


def _server_id(request: Request) -> int:
    try:
        server_id = int(request.args.get("server_id", "1"))
    except (TypeError, ValueError) as exc:
        raise BadRequest("server_id must be an integer") from exc
    if server_id <= 0:
        raise BadRequest("server_id must be positive")
    return server_id


def _page_context(page: str) -> dict[str, object]:
    section = EXAMPLE_SECTIONS["charts"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_charts_{page}",
        "active_section": "examples_charts",
        "example_category": "charts",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["OWNED_CHART_PAGES", "example_charts_page"]
