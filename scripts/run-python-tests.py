"""Run the example Python suite with an isolated tracked settings template."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Bootstrap the suite with one isolated service settings file."""
    with tempfile.TemporaryDirectory(prefix="oldman-epg-tests-") as temporary_directory:
        state_root = Path(temporary_directory)
        settings_file = state_root / "web_settings.yaml"
        shutil.copy2(
            ROOT / "data" / "web_settings.example.yaml",
            settings_file,
        )
        payload = YAML(typ="safe", pure=True).load(settings_file.read_text(encoding="utf-8"))
        payload["database"]["url"] = f"sqlite+aiosqlite:///{state_root / 'epg-tests.db'}"
        yaml = YAML()
        with settings_file.open("w", encoding="utf-8") as file:
            yaml.dump(payload, file)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "oldman.cli",
                "web",
                "settings",
                "sync",
                "--config",
                str(settings_file),
            ],
            cwd=ROOT,
            check=True,
        )
        runner = textwrap.dedent(
            """
            import sys
            import unittest

            from oldman import bootstrap_service

            bootstrap_service("web", config_file=sys.argv[1])
            suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            raise SystemExit(0 if result.wasSuccessful() else 1)
            """
        )
        return_code = 0
        for test_file in sorted((ROOT / "tests").glob("test_*.py")):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    runner,
                    str(settings_file),
                    f"tests.{test_file.stem}",
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                check=False,
            )
            return_code = max(return_code, completed.returncode)
        return return_code


if __name__ == "__main__":
    raise SystemExit(main())
