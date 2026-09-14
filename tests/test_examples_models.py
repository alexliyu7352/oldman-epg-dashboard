"""Contracts for the Dashboard examples App and its real data model."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExampleModelTests(unittest.TestCase):
    """Load the App in isolation so process-wide ORM state cannot leak."""

    def test_registry_loads_the_complete_managed_example_schema(self) -> None:
        source = """
            from sqlalchemy import CheckConstraint, Text, UniqueConstraint

            from oldman.apps import AppRegistry
            from oldman.storage.models import _get_model_file_config

            registry = AppRegistry()
            registry.register_packages(("apps.examples",))
            registry.load_models()

            app = registry.get_by_label("examples")
            assert app.icon == "ri-flask-line"
            assert str(app.display_name) == "Dashboard Examples"

            metadata = {item.model.__name__: item for item in registry.models}
            assert set(metadata) == {
                "ExampleTeam",
                "ExampleProject",
                "ExampleTask",
                "ExampleTag",
                "ExampleProjectTag",
                "ExampleAsset",
                "ExampleServer",
                "ExampleServerMetric",
                "ExampleLogo",
                "ExampleStreamProfile",
            }
            assert all(item.app_label == "examples" for item in metadata.values())
            assert all(item.managed for item in metadata.values())
            assert all(item.table.name.startswith("example_") for item in metadata.values())
            assert all(str(item.verbose_name) for item in metadata.values())
            assert all(str(item.verbose_name_plural) for item in metadata.values())

            tables = {item.table.name: item.table for item in metadata.values()}
            expected_foreign_keys = {
                ("example_project", "team_id", "example_team.id"),
                ("example_task", "project_id", "example_project.id"),
                ("example_project_tag", "project_id", "example_project.id"),
                ("example_project_tag", "tag_id", "example_tag.id"),
                ("example_asset", "project_id", "example_project.id"),
                ("example_server_metric", "server_id", "example_server.id"),
                ("example_stream_profile", "logo_id", "example_logo.id"),
            }
            actual_foreign_keys = {
                (table.name, column.name, foreign_key.target_fullname)
                for table in tables.values()
                for column in table.columns
                for foreign_key in column.foreign_keys
            }
            assert actual_foreign_keys == expected_foreign_keys

            project_tag = tables["example_project_tag"]
            assert any(
                isinstance(constraint, UniqueConstraint)
                and tuple(column.name for column in constraint.columns) == ("project_id", "tag_id")
                for constraint in project_tag.constraints
            )
            assert any(
                isinstance(constraint, CheckConstraint)
                for constraint in tables["example_project"].constraints
            )
            assert isinstance(tables["example_project"].c.metadata_json.type, Text)
            assert isinstance(tables["example_stream_profile"].c.sources_json.type, Text)

            asset = tables["example_asset"]
            document = _get_model_file_config(asset.c.document_path)
            preview = _get_model_file_config(asset.c.preview_path)
            assert document is not None and document.storage == "default"
            assert preview is not None and preview.storage == "default"
            assert document.upload_to == "examples/assets"
            assert preview.upload_to == "examples/previews"
        """
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(ROOT), environment.get("PYTHONPATH")))
        )

        completed = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
