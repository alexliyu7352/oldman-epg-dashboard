"""The accounts App is the skeleton's: the files `startproject` generates for a dashboard are here unchanged."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from oldman.cli.scaffold import DatabaseChoice, ProjectType, start_project

ROOT = Path(__file__).resolve().parents[1]
# The name the skeleton writes into models.py; fixed, so a checkout under another directory name compares the same.
PROJECT_NAME = "oldman_epg_dashboard"
SKELETON_FILES = (
    "apps/accounts/__init__.py",
    "apps/accounts/apps.py",
    "apps/accounts/models.py",
    "apps/accounts/migrations/__init__.py",
)


class AccountsAppMatchesTheSkeletonTest(unittest.TestCase):
    def test_the_skeleton_owned_files_are_unchanged(self) -> None:
        """Change them in the framework's skeleton, then regenerate; the routes and the rest are this project's."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            previous = Path.cwd()
            os.chdir(temporary_directory)
            try:
                generated = start_project(PROJECT_NAME, project_type=ProjectType.DASHBOARD, db=DatabaseChoice.SQLITE)
            finally:
                os.chdir(previous)
            for relative in SKELETON_FILES:
                with self.subTest(file=relative):
                    self.assertEqual(
                        (generated / relative).read_text(encoding="utf-8"),
                        (ROOT / relative).read_text(encoding="utf-8"),
                    )


if __name__ == "__main__":
    unittest.main()
