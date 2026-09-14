"""Finite command examples for process APIs, not another worker service."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from enum import StrEnum
import json
from pathlib import Path
import sys
import tempfile
from typing import Annotated

import typer
from sqlalchemy import select

from oldman.cli import Command
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.processes import (
    AsyncProcessManager, CompletedSubprocess, SubprocessError, SubprocessTimeoutError,
    create_subprocess_exec, run_subprocess_exec,
)

from .models import ExampleProject
from .process_jobs import inspect_projects


class ProcessScenario(StrEnum):
    """Only these fixed demonstration paths can be selected from the CLI."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCEL = "cancel"


async def _project_statuses() -> list[str]:
    """Only the parent reads the database; both child APIs receive plain values."""
    async with db_manager.get_read_session() as session:
        return list(await session.scalars(select(ExampleProject.status)))


class PythonProcessDemo(Command):
    """Pass plain database values to one importable child and observe real cleanup."""

    name = "python-process"
    help = _("Run a short Python process with real output, failure, timeout or cancellation.")

    async def handle(
        self, scenario: Annotated[ProcessScenario, typer.Option()] = ProcessScenario.SUCCESS,
    ) -> None:
        """Own one manager and its marker directory until all child work has stopped."""
        manager = AsyncProcessManager(workers=1)
        job: asyncio.Task | None = None
        with tempfile.TemporaryDirectory(prefix="oldman-python-demo-", dir="/tmp") as temporary:
            marker = Path(temporary) / "pid"
            try:
                statuses = await _project_statuses()
                job = asyncio.create_task(manager.run_with_timeout(
                    inspect_projects, (statuses, scenario.value, str(marker)),
                    _timeout=1 if scenario == ProcessScenario.TIMEOUT else 10,
                ))
                if scenario == ProcessScenario.CANCEL:
                    # Cancel after target entry, not merely before spawn has begun.
                    async with asyncio.timeout(5):
                        while not marker.exists() or not marker.read_text():
                            await asyncio.sleep(.02)
                    job.cancel()
                try:
                    result = await job
                except asyncio.CancelledError:
                    if scenario != ProcessScenario.CANCEL:
                        raise
                    typer.echo("CancelledError")
                else:
                    if not result or "counts" not in result:
                        raise RuntimeError(f"Child did not return project counts: {result!r}; see output above")
                    typer.echo(json.dumps(result))
            finally:
                try:
                    if job is not None and not job.done():
                        job.cancel()
                        with suppress(Exception, asyncio.CancelledError):
                            await job
                    await manager.shutdown()
                finally:
                    await db_manager.close()
                if marker.exists():
                    pid = int(marker.read_text())
                    if Path(f"/proc/{pid}").exists():
                        raise RuntimeError(f"Python child {pid} was not reaped")
                    typer.echo(f"child_pid={pid} reaped=true")


def _show_subprocess_output(result: CompletedSubprocess | SubprocessTimeoutError) -> None:
    """Retain actual process identity and both output streams, including errors."""
    typer.echo(f"command_pid={result.pid} process_group={result.process_group}")
    if result.stdout:
        typer.echo(result.stdout.decode("utf-8", errors="replace"), nl=False)
    if result.stderr:
        typer.echo(result.stderr.decode("utf-8", errors="replace"), err=True, nl=False)


class SubprocessDemo(Command):
    """Run only the fixed example module and let Oldman own its process group."""

    name = "subprocess-demo"
    help = _("Run a fixed external command and observe output and process-group cleanup.")

    async def handle(
        self, scenario: Annotated[ProcessScenario, typer.Option()] = ProcessScenario.SUCCESS,
    ) -> None:
        """Show one-shot capture and cancellable streaming without a shell or custom signals."""
        command = (sys.executable, "-m", "apps.examples.external_job", scenario.value)
        try:
            data = json.dumps(await _project_statuses()).encode("utf-8")
            if scenario == ProcessScenario.CANCEL:
                process = await create_subprocess_exec(
                    *command, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                async with process:
                    assert process.stdout is not None
                    async with asyncio.timeout(5):
                        started = await process.stdout.readline()
                    if not started:
                        raise RuntimeError("External program exited before reporting startup")
                    typer.echo(started.decode("utf-8"), nl=False)
                    job = asyncio.create_task(process.communicate(data))
                    await asyncio.sleep(0)  # Let communicate enter before requesting cancellation.
                    job.cancel()
                    try:
                        await job
                    except asyncio.CancelledError:
                        typer.echo("CancelledError")
                typer.echo(f"command_pid={process.pid} process_group={process.process_group}")
            else:
                try:
                    result = await run_subprocess_exec(
                        *command, input=data, capture_output=True, check=True,
                        timeout=1 if scenario == ProcessScenario.TIMEOUT else 10,
                    )
                except SubprocessError as error:
                    _show_subprocess_output(error.result)
                    raise
                except SubprocessTimeoutError as error:
                    _show_subprocess_output(error)
                    raise
                _show_subprocess_output(result)
        finally:
            await db_manager.close()
