"""Real database and Storage work using native Taskiq decorators and kickers.

Only Worker/Scheduler discover this module automatically. The Web example
imports it explicitly after checking that distributed tasks are enabled.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from apps.examples.models import ExampleProject, ExampleTask
from oldman.conf import settings
from oldman.db import db_manager
from oldman.providers.redis import redis_client
from oldman.storage import storages
from oldman.tasks.distributed import broker

logger = logging.getLogger(__name__)
project_counts: dict[str, int] = {}


@broker.task(queue_name="reports")
async def project_rpc(project_id: int) -> dict[str, object]:
    """Use the Worker-owned Core connection, without changing existing tasks."""
    from apps.examples.nats_example import query_project_status

    reply = await query_project_status(project_id, "monitor_a")
    return {"reply": reply.to_dict(), "worker_pid": os.getpid()}


@broker.task(queue_name="reports")
async def project_summary(project_id: int, fail: bool = False) -> dict[str, object]:
    """Read a real project; the short delay makes asynchronous execution visible."""
    await asyncio.sleep(1)
    async with db_manager.get_read_session() as session:
        project = await session.get(ExampleProject, project_id)
        if project is None:
            raise ValueError("The selected project no longer exists")
        count = await session.scalar(select(func.count()).select_from(ExampleTask).where(ExampleTask.project_id == project_id))
        if fail:
            raise ValueError("Intentional Demo failure after reading the selected project")
        result: dict[str, object] = {
            "project_id": project.id, "name": project.name, "status": project.status,
            "budget": str(project.budget), "task_count": count, "worker_pid": os.getpid(),
            "calculated_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    logger.info("Project summary: %s", result)
    return result


@broker.task(queue_name="exports")
async def export_project(project_id: int, user_id: int, export_id: str) -> dict[str, object]:
    """Return a Storage logical name, never put file bytes or ORM objects in NATS."""
    summary = await project_summary(project_id)  # Direct call shares this task's execution slot.
    name = await storages.using("default").save(
        f"task-exports/{user_id}/{export_id}.json",
        json.dumps(summary, ensure_ascii=False, indent=2).encode(),
    )
    return {"project_id": project_id, "file": name, "worker_pid": os.getpid()}


@broker.task(queue_name="reports", retry_on_error=True, max_retries=3, delay=5)
async def retry_summary(project_id: int, demonstration_id: str) -> dict[str, object]:
    """Fail once per explicit demonstration, then let native SmartRetry reschedule."""
    client = await redis_client.using(settings.taskiq.redis_alias).async_get_conn()
    key = f"oldman_demo:{settings.taskiq.namespace}:retry:{demonstration_id}"
    attempts = await client.incr(key)
    if attempts == 1:
        await client.expire(key, 86400)
        raise ValueError("Intentional first attempt; Scheduler will publish the retry")
    summary = await project_summary(project_id)
    summary["attempts"] = attempts
    return summary


@broker.task(queue_name="reports")
async def complete_example_task(record_id: int) -> dict[str, object]:
    """Only the winning conditional UPDATE performs the business transition."""
    async with db_manager.get_session() as session:
        result = await session.execute(
            update(ExampleTask).where(ExampleTask.id == record_id, ExampleTask.is_completed.is_(False))
            .values(is_completed=True, status="done")
        )
        changed = cast(CursorResult, result).rowcount == 1
        record = await session.get(ExampleTask, record_id)
        if record is None:
            raise ValueError("The selected task record no longer exists")
        title = record.title
    logger.info("Business completion record=%s changed=%s pid=%s", record_id, changed, os.getpid())
    return {"record_id": record_id, "title": title, "changed": changed, "worker_pid": os.getpid()}


@broker.task(queue_name="reports", ignore_result=True, schedule=[{"interval": 300}])
async def refresh_project_counts() -> None:
    """Refresh this process only; broadcast explicitly to refresh all online peers."""
    async with db_manager.get_read_session() as session:
        rows = await session.execute(select(ExampleProject.status, func.count()).group_by(ExampleProject.status))
        project_counts.clear()
        project_counts.update({status: count for status, count in rows})
    logger.info("Demo process cache refreshed pid=%s counts=%s", os.getpid(), project_counts)
