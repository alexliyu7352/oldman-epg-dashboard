"""Real Demo services and SQLite fixture on an owned Core NATS server.

Run with NATS_SERVER=/path/to/nats-server; never accepts a live server URL.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
NATS_SERVER = os.environ.get("NATS_SERVER") or shutil.which("nats-server")


def copy_demo_sources(root: Path) -> None:
    """Copy only application Python and fixtures, never local YAML/DB/static/logs."""
    shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
    for directory in ("apps", "config", "services"):
        for source in (ROOT / directory).rglob("*.py"):
            target = root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for source in (ROOT / "apps").glob("*/fixtures/*.json"):
        target = root / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


@unittest.skipUnless(NATS_SERVER, "Real communication examples need an owned NATS binary")
class NatsExampleTest(unittest.TestCase):
    """Exercise both Simple entrypoints, not handlers called in the test process."""

    def test_receivers_query_migrated_fixture_and_stop(self) -> None:
        """Initialize all five configurations as documented; verify real rows/PIDs."""
        with tempfile.TemporaryDirectory(prefix="oldman-demo-nats-", dir="/tmp") as directory, ExitStack() as stack:
            root = Path(directory)
            copy_demo_sources(root)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            processes: list[subprocess.Popen] = []

            def spawn(name: str, args: list[str]) -> subprocess.Popen:
                """Every child owns a new group and writes only to this temporary root."""
                log = stack.enter_context((root / f"{name}.log").open("w"))
                process = subprocess.Popen(args, cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                processes.append(process)
                return process

            def cli(*args: str, input: str = "") -> str:
                """Use the public CLI, including its real configuration and migration path."""
                command = [sys.executable, "-m", "oldman.cli", *args]
                if input:
                    # Linux script supplies a real PTY; do not bypass migration's
                    # deliberate terminal requirement or change production prompts.
                    command = ["script", "-q", "-e", "-c", shlex.join(command), "/dev/null"]
                result = subprocess.run(command,
                                        cwd=root, input=input, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            try:
                spawn("nats", [str(NATS_SERVER), "-a", "127.0.0.1", "-p", str(port)])
                for attempt in range(100):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        if attempt == 99:
                            self.fail("Owned NATS did not start")
                        time.sleep(0.02)
                (root / "data").mkdir()
                for service in ("web", "task_worker", "task_scheduler", "nats_a", "nats_b"):
                    payload = YAML(typ="safe", pure=True).load(
                        (ROOT / "data" / f"{service}_settings.example.yaml").read_text()
                    )
                    payload["database"]["url"] = f"sqlite+aiosqlite:///{root / 'data' / 'demo.db'}"
                    payload["nats"]["TASKIQ"]["nats_url"] = f"nats://127.0.0.1:{port}"
                    payload["nats_bus"]["namespace"] = "demo_validation"
                    payload["taskiq"]["enabled"] = False
                    with (root / "data" / f"{service}_settings.yaml").open("w") as file:
                        YAML().dump(payload, file)
                    cli(service, "settings", "sync")
                cli("db", "migrate", input="1\n1\n")
                cli("web", "loaddata", "demo")
                receivers = [spawn(name, [sys.executable, "-m", "oldman.cli", name, "start"])
                             for name in ("nats_a", "nats_b")]
                probe = textwrap.dedent('''
                    import asyncio, os, sqlite3, sys
                    from oldman import bootstrap_service
                    bootstrap_service("web")
                    from pathlib import Path
                    from oldman.runtime.discovery import discover_service_definitions
                    definitions = discover_service_definitions(Path.cwd())
                    assert tuple(definitions) == ("nats_a", "nats_b", "task_scheduler", "task_worker", "web")
                    assert definitions["nats_a"].application_base == definitions["nats_b"].application_base == "simple"
                    from oldman.providers.nats import bus
                    from nats.errors import NoRespondersError
                    from apps.examples.nats_example import query_project_status, query_observations
                    async def main():
                        with sqlite3.connect("data/demo.db") as database:
                            row = database.execute("select id, name, status, progress from example_project order by id limit 1").fetchone()
                            count = database.execute("select count(*) from example_task where project_id=?", (row[0],)).fetchone()[0]
                        assert row is not None
                        pids = []
                        async with bus:
                            for peer in ("monitor_a", "monitor_b"):
                                async with asyncio.timeout(15):
                                    while True:
                                        try:
                                            result = await query_project_status(row[0], peer)
                                            break
                                        except NoRespondersError:
                                            await asyncio.sleep(.05)
                                assert (result.project_id, result.name, result.status, result.progress) == row
                                assert result.task_count == count and result.found
                                assert result.peer_id == peer and result.pid != os.getpid()
                                pids.append(result.pid)
                                missing = await query_project_status(-1, peer)
                                assert not missing.found and missing.pid == result.pid
                                snapshot = await query_observations(peer)
                                assert snapshot.pid == result.pid
                                assert (snapshot.compete, snapshot.broadcast, snapshot.reports) == (0, 0, 0)
                                print(peer, result.to_dict(), flush=True)
                        assert len(set(pids)) == 2
                        assert not any(name.startswith("apps.communication") for name in sys.modules)
                    asyncio.run(main())
                ''')
                result = subprocess.run([sys.executable, "-c", probe], cwd=root,
                                        text=True, capture_output=True, timeout=25)
                receiver_logs = "\n".join((root / f"{name}.log").read_text() for name in ("nats_a", "nats_b"))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr + receiver_logs)
                print(result.stdout.strip())
                for name, receiver in zip(("nats_a", "nats_b"), receivers, strict=True):
                    cli(name, "stop")
                    self.assertEqual(receiver.wait(timeout=10), 0)
                    log = (root / f"{name}.log").read_text()
                    self.assertNotIn("Task was destroyed", log)
                    self.assertNotIn("Event loop stopped before", log)
                    self.assertNotIn("[ERROR]", log)
            finally:
                # Failure cleanup is not evidence of a successful normal stop.
                for process in reversed(processes):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
