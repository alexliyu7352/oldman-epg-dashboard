"""Finite owner of a real fixed Worker, sharing only serializable project records."""

import asyncio
from enum import StrEnum
import json
from pathlib import Path
import tempfile
from typing import Annotated

import typer
from sqlalchemy import select

from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.tasks import BaseManager, TaskType

from .models import ExampleProject
from .worker_jobs import ProjectSnapshotWorker


class WorkerScenario(StrEnum):
    """Fixed scenarios avoid exposing arbitrary task data or filesystem destinations."""

    SUCCESS = "success"
    ERROR = "error"
    STOP = "stop"


async def _read_report(path: Path) -> object:
    """Wait for this Demo's actual output, not an assumed completion from a task ID."""
    async with asyncio.timeout(10):
        while not path.exists():
            await asyncio.sleep(.02)
    return json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))


class WorkerDemo(Command):
    """Start one fixed Worker, submit an export, stop it and verify process cleanup."""

    name = "worker-demo"
    help = _("Run a fixed Worker and inspect a real project snapshot, failure or task stop.")

    async def handle(
        self, scenario: Annotated[WorkerScenario, typer.Option()] = WorkerScenario.SUCCESS,
    ) -> None:
        """Own the manager, output directory and parent database for this command only."""
        manager = BaseManager(ProjectSnapshotWorker, num_workers=1)
        pids: list[int] = []
        with tempfile.TemporaryDirectory(prefix="oldman-worker-demo-", dir="/tmp") as directory:
            try:
                async with db_manager.get_read_session() as session:
                    records = [dict(row) for row in (await session.execute(
                        select(ExampleProject.id, ExampleProject.name, ExampleProject.status).order_by(ExampleProject.id)
                    )).mappings()]
                if scenario == WorkerScenario.ERROR:
                    records.append({"id": "invalid", "name": "Invalid input", "status": "invalid"})
                await manager.start()
                pids = [info.process.pid for info in manager.workers.values() if info.process.pid is not None]
                typer.echo(json.dumps(manager.get_manager_status()))
                task_id = await manager.add_task({
                    "task_type": "project_snapshot", "directory": directory, "projects": records,
                    "wait_for_stop": scenario == WorkerScenario.STOP,
                }, task_type=TaskType.PERSISTENT if scenario == WorkerScenario.STOP else TaskType.ONE_TIME)
                if task_id is None:
                    raise RuntimeError("No fixed Worker accepted the export command")
                report = await _read_report(Path(directory) / f"{task_id}.json")
                if not isinstance(report, dict) or report.get("worker_pid") not in pids:
                    raise RuntimeError("Missing report from the actual Worker process")
                await manager.stop_task(task_id)
                if await _read_report(Path(directory) / f"{task_id}.stopped") is not True:
                    raise RuntimeError("The export coroutine did not finish cleanup")
                if "error" in report:
                    raise ValueError(report["error"])
                projects = report["projects"]
                typer.echo(json.dumps({
                    "task_id": task_id, "worker_pid": report["worker_pid"],
                    "project_count": len(projects), "first_project": projects[0] if projects else None,
                    "execute_cleanup_completed": True,
                }, ensure_ascii=False))
            finally:
                try:
                    await manager.shutdown()
                finally:
                    await db_manager.close()
                for pid in pids:
                    if Path(f"/proc/{pid}").exists():
                        raise RuntimeError(f"Fixed Worker {pid} was not reaped")
                    typer.echo(f"worker_pid={pid} reaped=true")
