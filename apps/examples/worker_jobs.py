"""Importable fixed Worker and its local file-export task, without ORM state."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from oldman.tasks import BaseTask, BaseWorker


def _write_json(path: Path, value: object) -> None:
    """Publish one small report atomically so the parent never reads half a JSON file."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class ProjectSnapshotTask(BaseTask):
    """Export actual input rows; the waiting mode demonstrates explicit task stop."""

    def __init__(self, task_id: str, data: dict[str, Any]) -> None:
        super().__init__(task_id)
        self.directory = Path(data["directory"])
        self.projects = data["projects"]
        self.wait_for_stop = data["wait_for_stop"]

    async def execute(self) -> None:
        """Keep task cancellation cleanup inside the coroutine, not the earlier cleanup hook."""
        try:
            try:
                rows = [{"id": int(row["id"]), "name": row["name"], "status": row["status"]}
                        for row in self.projects]
            except (ValueError, TypeError) as error:
                report = {"task_id": self.task_id, "worker_pid": os.getpid(), "error": str(error)}
            else:
                report = {"task_id": self.task_id, "worker_pid": os.getpid(), "projects": rows}
            await asyncio.to_thread(_write_json, self.directory / f"{self.task_id}.json", report)
            if self.wait_for_stop:
                await asyncio.Event().wait()
        finally:
            await asyncio.to_thread(_write_json, self.directory / f"{self.task_id}.stopped", True)


def create_snapshot(task_id: str, data: dict[str, Any]) -> BaseTask:
    """The existing Worker creator contract is synchronous, even for async tasks."""
    return ProjectSnapshotTask(task_id, data)


class ProjectSnapshotWorker(BaseWorker):
    """Register one task creator; no custom queue, ACK or result backend."""

    async def initialize(self) -> None:
        """Registration happens in this spawned Worker, not in the Web process."""
        self.register_task_creator("project_snapshot", create_snapshot)
