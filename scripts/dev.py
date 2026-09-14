#!/usr/bin/env python3
"""Run the EPG Dashboard backend and Vite source server together."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"


def require_environment() -> None:
    """Require bootstrap and the service settings before local development."""
    if not PYTHON.is_file():
        raise RuntimeError("Dashboard virtual environment is unavailable; run python3 scripts/bootstrap.py first")
    if not (ROOT / "data" / "web_settings.yaml").is_file():
        raise RuntimeError("Copy data/web_settings.example.yaml to data/web_settings.yaml before starting the Demo")


def oldman_template_directory() -> Path:
    """Resolve templates from the Python package selected by this Demo environment."""
    completed = subprocess.run(
        [str(PYTHON), "-c", "import pathlib, oldman; print(pathlib.Path(oldman.__file__).parent / 'web/templates')"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    path = Path(completed.stdout.strip()).resolve()
    if not (path / "oldman" / "dashboard" / "base.html").is_file():
        raise RuntimeError(f"Oldman dashboard templates were not found: {path}")
    return path


def start(command: list[str], *, environment: dict[str, str]) -> subprocess.Popen[bytes]:
    """Start one child in its own process group for reliable cleanup."""
    return subprocess.Popen(command, cwd=ROOT, env=environment, start_new_session=True)


def stop_all(processes: list[subprocess.Popen[bytes]]) -> None:
    """Stop every owned child without touching unrelated development processes."""
    for process in processes:
        if process.poll() is not None:
            continue
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


def main() -> int:
    """Run both source servers until one exits, then clean up the stack."""
    require_environment()
    environment = dict(os.environ)
    environment["OLDMAN_DEV"] = "1"
    environment["OLDMAN_PYTHON_TEMPLATE_DIR"] = str(oldman_template_directory())
    processes = [
        start(["pnpm", "--dir", "frontend", "dev"], environment=environment),
        start([str(ROOT / "run.sh"), "web", "start"], environment=environment),
    ]
    exit_code = 0
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        exit_code = next(
            (process.returncode for process in processes if process.returncode is not None),
            1,
        )
    except KeyboardInterrupt:
        exit_code = 0
    finally:
        stop_all(processes)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
