"""Run both process CLI demos against the configured Demo without changing its data."""

import os
from pathlib import Path
import re
import subprocess


def main() -> None:
    """Require observed results/exceptions and reaped children, not only exit codes."""
    root = Path(__file__).resolve().parents[1]
    for command in ("python-process", "subprocess-demo"):
        for scenario, exit_code, evidence in (
            ("success", 0, '"counts":'),
            ("error", 1, "None" if command == "python-process" else "returned 7"),
            ("timeout", 1, "timeout"),
            ("cancel", 0, "CancelledError"),
        ):
            result = subprocess.run(
                [str(root / "run.sh"), "web", command, "--scenario", scenario],
                cwd=root, capture_output=True, text=True, timeout=20,
            )
            output = result.stdout + result.stderr
            assert result.returncode == exit_code, output
            assert evidence in output, output
            if command == "python-process":
                match = re.search(r"child_pid=(\d+) reaped=true", output)
                assert match is not None, output
                assert not Path(f"/proc/{match[1]}").exists(), output
            else:
                match = re.search(r"process_group=(\d+)", output)
                assert match is not None, output
                try:
                    os.killpg(int(match[1]), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError(f"Process group {match[1]} survived: {output}")
                descendant = re.search(r'"descendant_pid": (\d+)', output)
                if scenario in {"timeout", "cancel"}:
                    assert descendant is not None, output
                if descendant:
                    assert not Path(f"/proc/{descendant[1]}").exists(), output
            print(f"{command}/{scenario}: exit={exit_code}, expected outcome observed, resources reaped")


if __name__ == "__main__":
    main()
