#!/usr/bin/env python3
"""Prepare this Demo with local Oldman sources when they are available."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = ROOT / ".local"
LOCAL_OLDMAN = LOCAL_ROOT / "oldman"
SIBLING_OLDMAN_NAMES = ("oldman_framwork", "oldman")
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def run(*command: str, cwd: Path = ROOT) -> None:
    """Run one setup command and preserve its real failure."""
    subprocess.run(command, cwd=cwd, check=True)


def sibling_source_root() -> Path | None:
    """Return the first conventional sibling checkout that contains the framework source."""
    for name in SIBLING_OLDMAN_NAMES:
        candidate = ROOT.parent / name
        if (candidate / "oldman" / "__init__.py").is_file():
            return candidate
    return None


def ensure_local_source_link() -> Path | None:
    """Link the conventional sibling checkout without replacing user state."""
    if LOCAL_OLDMAN.is_symlink():
        target = LOCAL_OLDMAN.resolve()
        if not (target / "oldman" / "__init__.py").is_file():
            raise RuntimeError(f"Invalid local Oldman source link: {LOCAL_OLDMAN} -> {target}")
        return target
    if LOCAL_OLDMAN.exists():
        raise RuntimeError(f"Local Oldman path exists but is not a symlink: {LOCAL_OLDMAN}")
    sibling = sibling_source_root()
    if sibling is None:
        return None

    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    LOCAL_OLDMAN.symlink_to(os.path.relpath(sibling, LOCAL_ROOT), target_is_directory=True)
    return sibling.resolve()


def bootstrap_python(local_source: Path | None) -> None:
    """Create the Demo-owned environment and select source or release install."""
    if local_source is None:
        run("uv", "sync")
        return
    if not VENV_PYTHON.is_file():
        run("uv", "venv", ".venv", "--python", "3.12")
    run(
        "uv",
        "pip",
        "install",
        "--python",
        str(VENV_PYTHON),
        "--editable",
        str(local_source),
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        str(VENV_PYTHON),
        "sanic-testing>=24.6",
    )


def bootstrap_frontend(local_source: Path | None) -> None:
    """Install frontend dependencies without recording the local source path."""
    command = ["pnpm", "install"]
    if local_source is not None:
        command.append("--lockfile=false")
    run(*command, cwd=ROOT / "frontend")


def main() -> int:
    """Prepare both runtimes while keeping generated files inside the Demo."""
    local_source = ensure_local_source_link()
    bootstrap_python(local_source)
    bootstrap_frontend(local_source)
    source = local_source if local_source is not None else "published oldman/oldman-web packages"
    print(f"EPG Dashboard is ready with {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
