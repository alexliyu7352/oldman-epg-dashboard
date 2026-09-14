"""Real Core handlers loaded by Registry only in receiving Simple services."""

import asyncio
import os

from sqlalchemy import func, select

from apps.examples.models import ExampleProject, ExampleTask
from apps.examples.nats_messages import (
    ExampleEvent,
    ObservationReply,
    ObservationRequest,
    ProjectStatusReply,
    ProjectStatusRequest,
)
from oldman.conf import settings
from oldman.db import db_manager
from oldman.logging import logger
from oldman.providers.nats import bus

# Three fixed keys; no per-user/message history or unbounded sender collection.
counts = {"compete": 0, "broadcast": 0, "reports": 0}
senders = {"compete": "", "broadcast": "", "reports": ""}


@bus.subscriber("project.status", peer=True, queue="project-status-readers")
async def read_project_status(message: ProjectStatusRequest) -> ProjectStatusReply:
    """Return this process's actual database read and PID, including missing rows."""
    reply = ProjectStatusReply(
        project_id=message.project_id, found=False,
        peer_id=settings.nats_bus.peer_id or "", pid=os.getpid(),
    )
    async with db_manager.get_read_session() as session:
        project = await session.get(ExampleProject, message.project_id)
        if project is not None:
            reply.found = True
            reply.name, reply.status, reply.progress = project.name, project.status, project.progress
            reply.task_count = await session.scalar(
                select(func.count()).select_from(ExampleTask).where(ExampleTask.project_id == project.id)
            ) or 0
    return reply


@bus.subscriber("demo.events.compete", queue="demo-event-readers")
async def count_competing_event(message: ExampleEvent, peer_id: str) -> None:
    """Members of the same queue compete; the sequence is not a delivery receipt."""
    counts["compete"] += 1
    senders["compete"] = peer_id


@bus.subscriber("demo.events.broadcast")
async def count_broadcast_event(message: ExampleEvent, peer_id: str) -> None:
    """No queue: each online receiver counts a copy."""
    counts["broadcast"] += 1
    senders["broadcast"] = peer_id


@bus.subscriber("project.status.read", queue="project-status-loggers")
async def count_status_report(message: ProjectStatusReply, peer_id: str) -> None:
    """Observe a publisher result separately from the two event counters."""
    counts["reports"] += 1
    senders["reports"] = peer_id
    logger.info(f"Project status report from {peer_id}: {message}")


@bus.subscriber("demo.observations", peer=True, queue="demo-observers")
async def read_observations(message: ObservationRequest) -> ObservationReply:
    """Return only the current fixed-size snapshot; a restart resets counters."""
    return ObservationReply(
        peer_id=settings.nats_bus.peer_id or "", pid=os.getpid(),
        compete=counts["compete"], broadcast=counts["broadcast"], reports=counts["reports"],
        compete_sender=senders["compete"], broadcast_sender=senders["broadcast"],
        report_sender=senders["reports"],
    )


@bus.subscriber("demo.failure.slow", peer=True, queue="demo-failures")
async def slow_reply(message: ExampleEvent) -> ExampleEvent:
    """Caller timeout does not cancel this harmless two-second handler."""
    await asyncio.sleep(2)
    return message


@bus.subscriber("demo.failure.raise", peer=True, queue="demo-failures")
async def failed_reply(message: ExampleEvent) -> ExampleEvent:
    """FastStream logs this local failure; no remote exception protocol is added."""
    raise ValueError("Intentional Demo RPC handler failure")
