"""Fixed external program for the managed-subprocess example; no framework imports."""

from collections import Counter
import json
import os
import subprocess
import sys
import time


def main() -> None:
    """Read project statuses from stdin or wait with a same-group descendant."""
    scenario = sys.argv[1]
    descendant = None
    if scenario in {"timeout", "cancel"}:
        # Intentionally left for Oldman's owned group cleanup, not a detached daemon.
        descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    print(json.dumps({"pid": os.getpid(), "descendant_pid": descendant.pid if descendant else None}), flush=True)
    if descendant is not None:
        time.sleep(30)
    statuses = json.load(sys.stdin)
    if scenario == "error":
        print("Demonstration: external calculation failed", file=sys.stderr, flush=True)
        raise SystemExit(7)
    print(json.dumps({"counts": dict(sorted(Counter(statuses).items()))}), flush=True)


if __name__ == "__main__":
    main()
