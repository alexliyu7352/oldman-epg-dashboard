"""Import-safe synchronous targets: no Settings, ORM or parent connections."""

from collections import Counter
import os
from pathlib import Path
import time


def inspect_projects(statuses: list[str], scenario: str, marker: str) -> dict:
    """Summarize real input, or deliberately exercise a documented failure mode."""
    pid = os.getpid()
    Path(marker).write_text(str(pid), encoding="utf-8")
    print(f"Python child {pid}: received {len(statuses)} project statuses", flush=True)
    if scenario == "error":
        raise ValueError("Demonstration: child calculation failed")
    if scenario in {"timeout", "cancel"}:
        time.sleep(30)
    return {"pid": pid, "counts": dict(sorted(Counter(statuses).items()))}
