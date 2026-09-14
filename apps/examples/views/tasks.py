"""Staff-only task demonstrations using Form and ordered response actions."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import select
from taskiq.exceptions import SendTaskError
from taskiq_redis.exceptions import ResultIsMissingError

from apps.auth.decorators import admin_required
from apps.auth.session import dashboard_session
from apps.examples.models import ExampleProject, ExampleTask
from oldman.conf import settings
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.providers.redis import redis_client
from oldman.web import NotFound
from oldman.web.api import ApiErrorCode, DefaultApiFormResponse, ReplaceHtmlAction
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

app = get_app()
logger = logging.getLogger(__name__)
PAGES = {"results", "schedules", "queues"}
OPERATIONS = {"summary", "export", "fail", "ignored", "retry", "complete", "local", "broadcast", "time", "interval", "cron", "rpc"}


def _user_id(request: Request) -> int:
    """Narrow the authenticated Session identity without querying another User."""
    user_id = dashboard_session(request).user_id
    if user_id is None:
        raise RuntimeError("The staff guard must supply an authenticated user")
    return user_id


def _owner_prefix(request: Request) -> str:
    """Namespace Demo ownership separately from Taskiq's native result storage."""
    return f"oldman_demo:{settings.taskiq.namespace}:user:{_user_id(request)}"


async def _client():
    """Use the existing provider, not the broker's privately owned Redis pools."""
    return await redis_client.using(settings.taskiq.redis_alias).async_get_conn()


def _invalid(message, *, field: str | None = None):
    """Validation remains a HTTP 200 Form business error, not a transport error."""
    return api_response(DefaultApiFormResponse(error_code=ApiErrorCode.INVALID_REQUEST, message=message, errors={field: message} if field else {}))


async def _result(request: Request, **context):
    """Replace one shared result area; no custom browser task runner is needed."""
    template = request.app.ext.environment.get_template("pages/examples/tasks/_result.html")
    html = await template.render_async(**context)
    code = ApiErrorCode.INVALID_REQUEST if context.get("outcome") == "publication_error" else ApiErrorCode.OK
    return api_response(DefaultApiFormResponse(error_code=code, actions=[ReplaceHtmlAction(html=html, target="#task-result")]))


@app.get("/examples/tasks/<page:str>", name="example_tasks_page")
@add_csrf_token()
@admin_required()
async def example_tasks_page(request: Request, page: str):
    """Read choices and owned plans only; GET never publishes or schedules work."""
    if page not in PAGES:
        raise NotFound("Task example page was not found")
    section = EXAMPLE_SECTIONS["tasks"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    projects, records, schedules = [], [], []
    if settings.taskiq.enabled:
        async with db_manager.get_read_session() as session:
            projects = list((await session.execute(select(ExampleProject.id, ExampleProject.name).order_by(ExampleProject.name))).all())
            records = list((await session.execute(select(ExampleTask.id, ExampleTask.title, ExampleTask.is_completed).order_by(ExampleTask.id).limit(100))).all())
        client = await _client()
        schedules = [json.loads(value) for value in (await client.hgetall(f"{_owner_prefix(request)}:schedules")).values()]
    return await render_template(f"pages/examples/tasks/{page}.html", context={
        "active_page": f"examples_tasks_{page}", "active_section": "examples_tasks",
        "example_category": "tasks", "example_page": page, "example_page_title": pages[page],
        "example_section": section, "page_entry": "examples", "taskiq_enabled": settings.taskiq.enabled,
        "projects": projects, "task_records": records, "schedules": schedules,
        "result_ttl": settings.taskiq.result_ex_time,
        "nats_enabled": settings.nats_bus.enabled,
    })


@app.post("/examples/tasks/run/<operation:str>", name="example_task_run")
@csrf_protect()
@admin_required()
async def example_task_run(request: Request, operation: str):
    """Publish fixed Demo operations only, preserving unknown delivery outcomes."""
    if operation not in OPERATIONS:
        raise NotFound("Task example operation was not found")
    if not settings.taskiq.enabled:
        return _invalid(_("Enable Taskiq in this service before using the example."))
    if operation == "rpc" and not settings.nats_bus.enabled:
        return _invalid(_("Enable nats_bus in Web and Worker before submitting the RPC task."))
    from apps.examples import tasks
    from oldman.tasks.distributed import schedule_source

    project_id = record_id = 0
    if operation not in {"local", "broadcast", "interval", "cron"}:
        field = "record_id" if operation == "complete" else "project_id"
        raw = (request.form or {}).get(field, "")
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) <= 2147483647:
            return _invalid(_("Choose a valid database record."), field=field)
        project_id = record_id = int(raw)
        model = ExampleTask if field == "record_id" else ExampleProject
        async with db_manager.get_read_session() as session:
            if await session.get(model, project_id) is None:
                return _invalid(_("The selected record no longer exists."), field=field)
    identifier = uuid4().hex
    ignored = operation in {"ignored", "local", "broadcast", "interval", "cron"}
    ownership = {"task_id": identifier, "operation": operation, "ignored": ignored}
    try:
        client = await _client()
        prefix = _owner_prefix(request)
        # Reserve ownership before publication: even a lost PubAck may have sent
        # the task, so its creator must still be able to query that same ID.
        await client.set(f"{prefix}:task:{identifier}", json.dumps(ownership), ex=settings.taskiq.result_ex_time)
        if operation in {"time", "interval", "cron"}:
            if await client.hlen(f"{prefix}:schedules") >= 10:
                return _invalid(_("Cancel older example plans before adding more (limit 10)."))
            ownership["schedule_id"] = identifier
            await client.hset(f"{prefix}:schedules", identifier, json.dumps(ownership))
            if operation == "time":
                await tasks.project_summary.kicker().with_task_id(identifier).with_schedule_id(identifier).schedule_by_time(
                    schedule_source, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=20), project_id,
                )
            elif operation == "interval":
                await tasks.refresh_project_counts.kicker().with_schedule_id(identifier).schedule_by_interval(schedule_source, 60)
            else:
                await tasks.refresh_project_counts.kicker().with_schedule_id(identifier).schedule_by_cron(schedule_source, "*/2 * * * *")
            return await _result(request, outcome="scheduled", **ownership)
        if operation == "rpc":
            await tasks.project_rpc.kicker().with_task_id(identifier).kiq(project_id)
        elif operation == "export":
            await tasks.export_project.kicker().with_task_id(identifier).kiq(project_id, _user_id(request), identifier)
        elif operation == "retry":
            await tasks.retry_summary.kicker().with_task_id(identifier).kiq(project_id, identifier)
        elif operation == "complete":
            await tasks.complete_example_task.kicker().with_task_id(identifier).kiq(record_id)
        elif operation in {"local", "broadcast"}:
            await tasks.refresh_project_counts.kicker().with_task_id(identifier).with_labels(broadcast=operation == "broadcast").kiq()
        else:
            await tasks.project_summary.kicker().with_task_id(identifier).with_labels(ignore_result=ignored).kiq(project_id, fail=operation == "fail")
    except (SendTaskError, RedisError):
        logger.exception("Demo task publication failed, task_id=%s", identifier)
        return await _result(request, outcome="publication_error", **ownership)
    return await _result(request, outcome="published", **ownership)


@app.post("/examples/tasks/result/<task_id:str>", name="example_task_result")
@csrf_protect()
@admin_required()
async def example_task_result(request: Request, task_id: str):
    """Read only IDs owned by this user; missing ownership never grants access."""
    if not settings.taskiq.enabled or re.fullmatch(r"[a-f0-9]{32}", task_id) is None:
        raise NotFound("Task result was not found")
    client = await _client()
    raw = await client.get(f"{_owner_prefix(request)}:task:{task_id}")
    if raw is None:
        return await _result(request, outcome="unavailable")
    owned = json.loads(raw)
    if owned["ignored"]:
        return await _result(request, outcome="ignored", **owned)
    from oldman.tasks.distributed import broker

    try:
        # A single GET handles expiry atomically; EXISTS followed by GET races TTL.
        result = await broker.result_backend.get_result(task_id)
    except ResultIsMissingError:
        return await _result(request, outcome="unavailable", **owned)
    return await _result(request, outcome="failed" if result.is_err else "success", value=result.return_value, error=str(result.error) if result.is_err else "", **owned)


@app.post("/examples/tasks/cancel/<schedule_id:str>", name="example_task_cancel")
@csrf_protect()
@admin_required()
async def example_task_cancel(request: Request, schedule_id: str):
    """Cancel an owned plan, not already queued work or another user's schedule."""
    if not settings.taskiq.enabled:
        raise NotFound("Task schedule was not found")
    client = await _client()
    key = f"{_owner_prefix(request)}:schedules"
    if not await client.hexists(key, schedule_id):
        raise NotFound("Task schedule was not found")
    from oldman.tasks.distributed import schedule_source

    await schedule_source.delete_schedule(schedule_id)
    await client.hdel(key, schedule_id)
    return await _result(request, outcome="cancelled", schedule_id=schedule_id)
