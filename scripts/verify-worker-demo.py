"""Check fixed Worker completion, business failure and stop through the real CLI."""

from pathlib import Path
import re
import subprocess


def main() -> None:
    """The Demo must read a real child report and leave no live Worker."""
    root = Path(__file__).resolve().parents[1]
    for scenario, exit_code in (("success", 0), ("error", 1), ("stop", 0)):
        result = subprocess.run(
            [str(root / "run.sh"), "web", "worker-demo", "--scenario", scenario],
            cwd=root, capture_output=True, text=True, timeout=25,
        )
        output = result.stdout + result.stderr
        assert result.returncode == exit_code, output
        if scenario == "error":
            assert "invalid literal for int()" in output, output
        else:
            assert '"project_count":' in output, output
            assert '"execute_cleanup_completed": true' in output, output
        match = re.search(r"worker_pid=(\d+) reaped=true", output)
        assert match is not None, output
        assert not Path(f"/proc/{match[1]}").exists(), output
        print(f"{scenario}: exit={exit_code}, real report and coroutine cleanup observed, worker reaped")


if __name__ == "__main__":
    main()
