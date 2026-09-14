"""Database-backed chart payloads for the Dashboard examples."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections import defaultdict

from sqlalchemy import func, select

from apps.epg_admin.tables import is_authenticated_request
from oldman.i18n import gettext
from oldman.web.components.charts import (
    ChartResult,
    ChartSeries,
    ChartSummary,
    SQLAlchemyChartView,
    TailwindChartRenderer,
)
from oldman.web.components.charts.views import ChartInvalidRequest
from oldman.web.request import Request

from .models import (
    ExampleProject,
    ExampleServer,
    ExampleServerMetric,
    ExampleTask,
    ExampleTeam,
)

TREND_CHARTS = ("project-trend", "task-trend", "project-progress", "team-budget")
COMPOSITION_CHARTS = ("project-status", "task-priority", "project-completion")
DISTRIBUTION_CHARTS = (
    "project-stacked",
    "server-scatter",
    "server-bubble",
    "server-heatmap",
    "team-treemap",
)
STATE_CHARTS = ("states", "empty", "forbidden")
REALTIME_CHART = "server-realtime"
CHART_KEYS = frozenset(
    (
        *TREND_CHARTS,
        *COMPOSITION_CHARTS,
        *DISTRIBUTION_CHARTS,
        *STATE_CHARTS,
        REALTIME_CHART,
    )
)


class ExampleChartData(SQLAlchemyChartView):
    """Serve all example charts through one validated data endpoint."""

    renderer_class = TailwindChartRenderer
    route_name = "example_chart_data"
    route_path = "/examples/charts/data/<chart_key:str>"
    default_range = "30d"
    allowed_ranges = ("7d", "30d", "90d")
    chart_type = "line"
    allowed_chart_types = ("line",)
    _chart_key = ""

    async def get(self, request: Request, chart_key: str):
        """Capture and validate the route-owned chart key before normal handling."""
        if chart_key not in CHART_KEYS:
            return self.render_error_response(
                gettext("Unknown example chart", request=request), status=404
            )
        self._chart_key = chart_key
        return await super().get(request, chart_key=chart_key)

    async def check_auth(self, request: Request) -> bool:
        """Use the Dashboard guard and retain one deliberate 403 state."""
        return self._chart_key != "forbidden" and is_authenticated_request(request)

    async def filter_delay(self, _value: str) -> None:
        """Declare the states-page delay filter used to prove latest-wins loading."""

    async def get_result(self, chart_request) -> ChartResult:
        """Dispatch one fixed chart key without exposing arbitrary method lookup."""
        delay = chart_request.filters.get("delay")
        if delay:
            try:
                seconds = min(max(float(delay), 0), 1)
            except ValueError as exc:
                raise ChartInvalidRequest(
                    gettext("Invalid chart delay", request=chart_request.request)
                ) from exc
            await asyncio.sleep(seconds)

        handlers = {
            "project-trend": self._project_trend,
            "task-trend": self._task_trend,
            "project-progress": self._project_progress,
            "team-budget": self._team_budget,
            "project-status": self._project_status,
            "task-priority": self._task_priority,
            "project-completion": self._project_completion,
            "project-stacked": self._project_stacked,
            "server-scatter": self._server_scatter,
            "server-bubble": self._server_bubble,
            "server-heatmap": self._server_heatmap,
            "team-treemap": self._team_treemap,
            "states": self._project_status,
            "empty": self._empty,
            "server-realtime": self._server_realtime,
        }
        handler = handlers.get(self._chart_key)
        if handler is None:
            raise ChartInvalidRequest(
                gettext("Unknown example chart", request=chart_request.request)
            )
        result = await handler(chart_request)
        result.meta.setdefault("range", chart_request.range_key)
        result.meta.setdefault(
            "source", gettext("Example database", request=chart_request.request)
        )
        return result

    async def _project_trend(self, chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleProject.start_date, func.count(ExampleProject.id))
            .group_by(ExampleProject.start_date)
            .order_by(ExampleProject.start_date)
        )
        values = [
            (str(day), int(total)) for day, total in rows.all() if day is not None
        ]
        values = _range_tail(values, chart_request.range_key)
        return ChartResult(
            series=[
                ChartSeries(
                    name=gettext("Projects", request=self.request),
                    data=[total for _, total in values],
                )
            ],
            labels=[day for day, _ in values],
            summary=[
                ChartSummary(
                    label=gettext("Projects", request=self.request),
                    value=sum(total for _, total in values),
                    tone="primary",
                )
            ],
            chart={"type": "line", "height": 310, "toolbar": {"show": False}},
            options={
                "stroke": {"curve": "smooth", "width": 3},
                "xaxis": {"type": "datetime"},
            },
        )

    async def _task_trend(self, chart_request) -> ChartResult:
        date_expr = func.date(ExampleTask.due_at)
        rows = await self.require_db_session().execute(
            select(date_expr, func.count(ExampleTask.id))
            .where(ExampleTask.due_at.is_not(None))
            .group_by(date_expr)
            .order_by(date_expr)
        )
        values = _range_tail(
            [(str(day), int(total)) for day, total in rows.all()],
            chart_request.range_key,
        )
        return ChartResult(
            series=[
                ChartSeries(
                    name=gettext("Tasks due", request=self.request),
                    data=[total for _, total in values],
                )
            ],
            labels=[day for day, _ in values],
            summary=[
                ChartSummary(
                    label=gettext("Tasks", request=self.request),
                    value=sum(total for _, total in values),
                    tone="info",
                )
            ],
            chart={"type": "area", "height": 310, "toolbar": {"show": False}},
            options={
                "stroke": {"curve": "smooth", "width": 2},
                "fill": {"opacity": 0.25},
                "xaxis": {"type": "datetime"},
            },
        )

    async def _project_progress(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleProject.status, func.avg(ExampleProject.progress))
            .group_by(ExampleProject.status)
            .order_by(ExampleProject.status)
        )
        data = [
            {
                "x": _project_status_label(status, self.request),
                "y": round(float(average or 0), 1),
            }
            for status, average in rows.all()
        ]
        return _axis_result(
            gettext("Average progress", request=self.request),
            data,
            "bar",
            "warning",
            options={"plotOptions": {"bar": {"columnWidth": "55%"}}},
        )

    async def _team_budget(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleTeam.name, func.sum(ExampleProject.budget))
            .join(ExampleProject, ExampleProject.team_id == ExampleTeam.id)
            .group_by(ExampleTeam.id, ExampleTeam.name)
            .order_by(func.sum(ExampleProject.budget).desc())
        )
        data = [
            {"x": name, "y": round(float(total or 0), 2)} for name, total in rows.all()
        ]
        return _axis_result(
            gettext("Budget", request=self.request),
            data,
            "bar",
            "success",
            options={"plotOptions": {"bar": {"horizontal": True}}},
        )

    async def _project_status(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleProject.status, func.count(ExampleProject.id))
            .group_by(ExampleProject.status)
            .order_by(ExampleProject.status)
        )
        values = [
            (_project_status_label(status, self.request), int(total))
            for status, total in rows.all()
        ]
        return _scalar_result(
            gettext("Projects", request=self.request), values, "pie", "primary"
        )

    async def _task_priority(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleTask.priority, func.count(ExampleTask.id))
            .group_by(ExampleTask.priority)
            .order_by(ExampleTask.priority)
        )
        values = [
            (_task_priority_label(priority, self.request), int(total))
            for priority, total in rows.all()
        ]
        return _scalar_result(
            gettext("Tasks", request=self.request), values, "donut", "info"
        )

    async def _project_completion(self, _chart_request) -> ChartResult:
        average = await self.require_db_session().scalar(
            select(func.avg(ExampleProject.progress))
        )
        value = round(float(average or 0), 1)
        return ChartResult(
            series=[value],
            labels=[gettext("Average completion", request=self.request)],
            summary=[
                ChartSummary(
                    label=gettext("Average completion", request=self.request),
                    value=f"{value}%",
                    tone="success",
                )
            ],
            chart={"type": "radialBar", "height": 310},
            options={
                "plotOptions": {
                    "radialBar": {
                        "dataLabels": {
                            "total": {
                                "show": True,
                                "label": gettext("Projects", request=self.request),
                            }
                        }
                    }
                }
            },
        )

    async def _project_stacked(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(
                ExampleProject.status,
                ExampleProject.priority,
                func.count(ExampleProject.id),
            )
            .group_by(ExampleProject.status, ExampleProject.priority)
            .order_by(ExampleProject.status, ExampleProject.priority)
        )
        statuses: list[str] = []
        priorities: dict[str, dict[str, int]] = defaultdict(dict)
        for status, priority, total in rows.all():
            if status not in statuses:
                statuses.append(status)
            priorities[priority][status] = int(total)
        series = [
            ChartSeries(
                name=_task_priority_label(priority, self.request),
                data=[counts.get(status, 0) for status in statuses],
            )
            for priority, counts in sorted(priorities.items())
        ]
        return ChartResult(
            series=series,
            labels=[_project_status_label(status, self.request) for status in statuses],
            summary=[
                ChartSummary(
                    label=gettext("Projects", request=self.request),
                    value=sum(sum(item.data) for item in series),
                    tone="primary",
                )
            ],
            chart={
                "type": "bar",
                "height": 320,
                "stacked": True,
                "toolbar": {"show": False},
            },
            options={"plotOptions": {"bar": {"horizontal": False}}},
        )

    async def _server_scatter(self, _chart_request) -> ChartResult:
        metrics = await self._latest_server_metrics()
        data = [
            {"x": float(metric.cpu_percent), "y": float(metric.memory_percent)}
            for _, metric in metrics
        ]
        return ChartResult(
            series=[
                ChartSeries(name=gettext("Servers", request=self.request), data=data)
            ],
            summary=[
                ChartSummary(
                    label=gettext("Servers", request=self.request),
                    value=len(data),
                    tone="info",
                )
            ],
            chart={"type": "scatter", "height": 320, "toolbar": {"show": False}},
            options={
                "xaxis": {"title": {"text": gettext("CPU %", request=self.request)}},
                "yaxis": {
                    "title": {"text": gettext("Memory %", request=self.request)}
                },
            },
        )

    async def _server_bubble(self, _chart_request) -> ChartResult:
        metrics = await self._latest_server_metrics()
        data = [
            {
                "x": float(metric.upload_mbps),
                "y": float(metric.download_mbps),
                "z": float(server.bandwidth_mbps) / 100,
            }
            for server, metric in metrics
        ]
        return ChartResult(
            series=[
                ChartSeries(name=gettext("Servers", request=self.request), data=data)
            ],
            summary=[
                ChartSummary(
                    label=gettext("Servers", request=self.request),
                    value=len(data),
                    tone="warning",
                )
            ],
            chart={"type": "bubble", "height": 320, "toolbar": {"show": False}},
            options={
                "xaxis": {
                    "title": {"text": gettext("Upload Mbps", request=self.request)}
                },
                "yaxis": {
                    "title": {
                        "text": gettext("Download Mbps", request=self.request)
                    }
                },
            },
        )

    async def _server_heatmap(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(
                ExampleServer.name,
                ExampleServerMetric.sampled_at,
                ExampleServerMetric.cpu_percent,
            )
            .join(
                ExampleServerMetric, ExampleServerMetric.server_id == ExampleServer.id
            )
            .order_by(ExampleServerMetric.sampled_at.desc())
            .limit(48)
        )
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for name, sampled_at, cpu in reversed(rows.all()):
            grouped[name].append({"x": sampled_at.strftime("%H:%M"), "y": float(cpu)})
        series = [
            ChartSeries(name=name, data=data) for name, data in sorted(grouped.items())
        ]
        return ChartResult(
            series=series,
            summary=[
                ChartSummary(
                    label=gettext("Samples", request=self.request),
                    value=sum(len(item.data) for item in series),
                    tone="warning",
                )
            ],
            chart={"type": "heatmap", "height": 340, "toolbar": {"show": False}},
            options={"dataLabels": {"enabled": False}},
        )

    async def _team_treemap(self, _chart_request) -> ChartResult:
        rows = await self.require_db_session().execute(
            select(ExampleTeam.name, func.sum(ExampleProject.budget))
            .join(ExampleProject, ExampleProject.team_id == ExampleTeam.id)
            .group_by(ExampleTeam.id, ExampleTeam.name)
            .order_by(ExampleTeam.name)
        )
        data = [
            {"x": name, "y": round(float(total or 0), 2)} for name, total in rows.all()
        ]
        return ChartResult(
            series=[
                ChartSeries(name=gettext("Budget", request=self.request), data=data)
            ],
            summary=[
                ChartSummary(
                    label=gettext("Teams", request=self.request),
                    value=len(data),
                    tone="success",
                )
            ],
            chart={"type": "treemap", "height": 340, "toolbar": {"show": False}},
            options={"dataLabels": {"enabled": True}},
        )

    async def _server_realtime(self, chart_request) -> ChartResult:
        raw_server_id = chart_request.filters.get("server_id", "1")
        try:
            server_id = int(raw_server_id)
        except ValueError as exc:
            raise ChartInvalidRequest(
                gettext("Invalid server id", request=self.request)
            ) from exc
        return ChartResult(
            series=[
                ChartSeries(
                    name=gettext("CPU %", request=self.request),
                    data=[],
                ),
                ChartSeries(
                    name=gettext("Memory %", request=self.request),
                    data=[],
                ),
            ],
            meta={"server_id": server_id},
            chart={
                "type": "line",
                "height": 340,
                "animations": {"enabled": False},
                "toolbar": {"show": False},
            },
            options={
                "stroke": {"curve": "smooth", "width": 2},
                "xaxis": {"type": "datetime"},
            },
        )

    async def _empty(self, _chart_request) -> ChartResult:
        return ChartResult(
            series=[],
            summary=[ChartSummary(label=gettext("Rows", request=self.request), value=0)],
            meta={"state": "empty"},
        )

    async def _latest_server_metrics(
        self,
    ) -> list[tuple[ExampleServer, ExampleServerMetric]]:
        rows = await self.require_db_session().execute(
            select(ExampleServer, ExampleServerMetric)
            .join(
                ExampleServerMetric, ExampleServerMetric.server_id == ExampleServer.id
            )
            .order_by(ExampleServerMetric.sampled_at.desc())
        )
        latest: dict[int, tuple[ExampleServer, ExampleServerMetric]] = {}
        for server, metric in rows.all():
            latest.setdefault(server.id, (server, metric))
        return list(latest.values())


def _range_tail(values: list[tuple[str, int]], range_key: str) -> list[tuple[str, int]]:
    """Keep a range-sized tail while preserving fixture usefulness over time."""
    if not values:
        return values
    days = {"7d": 7, "30d": 30, "90d": 90}[range_key]
    end = dt.date.fromisoformat(values[-1][0])
    start = end - dt.timedelta(days=days - 1)
    return [
        (label, value)
        for label, value in values
        if dt.date.fromisoformat(label) >= start
    ]


def _project_status_label(value: str, request: Request | None) -> str:
    """Translate the fixed project states stored by the example fixture."""
    labels = {
        "active": gettext("Active", request=request),
        "completed": gettext("Completed", request=request),
        "paused": gettext("Paused", request=request),
        "planned": gettext("Planned", request=request),
        "review": gettext("Review", request=request),
    }
    return labels.get(value, value)


def _task_priority_label(value: str, request: Request | None) -> str:
    """Translate the fixed task priorities stored by the example fixture."""
    labels = {
        "critical": gettext("Critical", request=request),
        "high": gettext("High", request=request),
        "low": gettext("Low", request=request),
        "normal": gettext("Normal", request=request),
    }
    return labels.get(value, value)


def _axis_result(
    name: str,
    data: list[dict[str, object]],
    chart_type: str,
    tone: str,
    *,
    options: dict[str, object],
) -> ChartResult:
    return ChartResult(
        series=[ChartSeries(name=name, data=data)],
        labels=[str(item["x"]) for item in data],
        summary=[
            ChartSummary(
                label=name,
                value=round(sum(float(item["y"]) for item in data), 2),
                tone=tone,
            )
        ],
        chart={"type": chart_type, "height": 310, "toolbar": {"show": False}},
        options=options,
    )


def _scalar_result(
    name: str, values: list[tuple[str, int]], chart_type: str, tone: str
) -> ChartResult:
    return ChartResult(
        series=[value for _, value in values],
        labels=[label for label, _ in values],
        summary=[
            ChartSummary(label=name, value=sum(value for _, value in values), tone=tone)
        ],
        chart={"type": chart_type, "height": 310},
        options={"legend": {"position": "bottom"}},
    )


__all__ = [
    "CHART_KEYS",
    "COMPOSITION_CHARTS",
    "DISTRIBUTION_CHARTS",
    "ExampleChartData",
    "REALTIME_CHART",
    "STATE_CHARTS",
    "TREND_CHARTS",
]
