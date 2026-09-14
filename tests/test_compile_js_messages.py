"""Tests for the EPG adapter around unified frontend catalogs."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "compile_js_messages.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "compile_js_messages",
    SCRIPT_PATH,
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:
    raise RuntimeError(f"Cannot import {SCRIPT_PATH}")
compile_js_messages = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(compile_js_messages)


def write_settings(path: Path) -> None:
    """Write one compact standard-language test configuration."""
    path.write_text(
        textwrap.dedent(
            """
            i18n:
              default_language: zh-Hans
              languages:
                en: {}
                zh-Hans:
                  flag: cn
            """
        ).strip(),
        encoding="utf-8",
    )


class CompileJsMessagesTest(unittest.TestCase):
    """Keep only EPG-owned manifest and publication behavior in this repo."""

    def test_language_manifest_uses_profiles_and_standard_codes(self) -> None:
        """Compact settings inherit metadata without exposing Babel IDs."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            output_file = root / "languages.ts"
            write_settings(settings_file)

            compile_js_messages.write_language_manifest(
                output_file,
                settings_file,
            )
            output = output_file.read_text(encoding="utf-8")

        self.assertIn('export const defaultLanguage = "zh-Hans";', output)
        self.assertIn('code: "zh-Hans"', output)
        self.assertIn('locale: "zh-Hans"', output)
        self.assertIn('aliases: ["zh-CN", "zh-SG"]', output)
        self.assertIn('flagUrl: "/static/oldman/images/flags/cn.svg"', output)
        self.assertIn('catalogPath: "i18n/zh-hans.json"', output)
        self.assertNotIn("zh_Hans", output)

    def test_user_language_metadata_overrides_profiles(self) -> None:
        """Explicit aliases, names, and flags remain project-owned."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            output_file = root / "languages.ts"
            settings_file.write_text(
                textwrap.dedent(
                    """
                    i18n:
                      default_language: fr
                      languages:
                        fr:
                          aliases: [fr-FR]
                          name: Français
                          flag: images/fr.svg
                    """
                ).strip(),
                encoding="utf-8",
            )

            compile_js_messages.write_language_manifest(
                output_file,
                settings_file,
            )
            output = output_file.read_text(encoding="utf-8")

        self.assertIn('aliases: ["fr-FR"]', output)
        self.assertIn('name: "Français"', output)
        self.assertIn('flagUrl: "/static/images/fr.svg"', output)

    def test_unknown_default_language_is_rejected(self) -> None:
        """The language manifest must not silently choose another default."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_file = Path(temporary_directory) / "settings.yaml"
            settings_file.write_text(
                "i18n:\n"
                "  default_language: fr\n"
                "  languages:\n"
                "    en: {}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "default_language"):
                compile_js_messages.read_i18n_languages(settings_file)

    def test_language_catalogs_use_unified_po_and_filter_backend_ids(self) -> None:
        """The adapter reads messages.po and publishes only AST frontend IDs."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            locales_dir = root / "locales"
            output_dir = root / "public" / "i18n"
            write_settings(settings_file)
            _, configured = compile_js_messages.read_i18n_languages(
                settings_file
            )
            po_directory = locales_dir / "zh_Hans" / "LC_MESSAGES"
            po_directory.mkdir(parents=True)
            (po_directory / "messages.po").write_text(
                'msgid "Request failed"\n'
                'msgstr "请求失败"\n\n'
                'msgid "Backend only"\n'
                'msgstr "后端专用"\n',
                encoding="utf-8",
            )

            compile_js_messages.write_language_catalogs(
                output_dir,
                locales_dir,
                configured,
                project_root=ROOT,
            )
            english = json.loads(
                (output_dir / "en.json").read_text(encoding="utf-8")
            )
            chinese = json.loads(
                (output_dir / "zh-hans.json").read_text(encoding="utf-8")
            )

        self.assertEqual(english, {"locale": "en", "messages": {}})
        self.assertEqual(chinese["messages"]["Request failed"], "请求失败")
        self.assertNotIn("Backend only", chinese["messages"])

    def test_complete_publish_removes_stale_catalogs(self) -> None:
        """Every run publishes all configured languages and removes old JSON."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            locales_dir = root / "locales"
            output_dir = root / "public" / "i18n"
            manifest = root / "src" / "i18n" / "generated.ts"
            write_settings(settings_file)
            output_dir.mkdir(parents=True)
            (output_dir / "fr.json").write_text(
                '{"locale":"fr","messages":{}}\n',
                encoding="utf-8",
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("old manifest\n", encoding="utf-8")

            compile_js_messages.compile_frontend_i18n(
                output_dir,
                locales_dir,
                settings_file,
                manifest,
                project_root=ROOT,
            )

            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"en.json", "zh-hans.json"},
            )
            self.assertIn(
                'export const defaultLanguage = "zh-Hans";',
                manifest.read_text(encoding="utf-8"),
            )

    def test_compile_failure_preserves_previous_publication(self) -> None:
        """A failed staged compile cannot replace catalogs or their manifest."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            locales_dir = root / "locales"
            output_dir = root / "public" / "i18n"
            manifest = root / "src" / "i18n" / "generated.ts"
            write_settings(settings_file)
            po_directory = locales_dir / "zh_Hans" / "LC_MESSAGES"
            po_directory.mkdir(parents=True)
            (po_directory / "messages.po").write_text(
                'msgid "Request failed"\nmsgstr "请求失败"\n',
                encoding="utf-8",
            )
            output_dir.mkdir(parents=True)
            previous_catalog = '{"locale":"previous","messages":{}}\n'
            (output_dir / "previous.json").write_text(
                previous_catalog,
                encoding="utf-8",
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("previous manifest\n", encoding="utf-8")

            with (
                mock.patch.object(
                    compile_js_messages,
                    "compile_project_frontend_catalog",
                    side_effect=RuntimeError("compile failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "compile failed"),
            ):
                compile_js_messages.compile_frontend_i18n(
                    output_dir,
                    locales_dir,
                    settings_file,
                    manifest,
                    project_root=ROOT,
                )

            self.assertEqual(
                (output_dir / "previous.json").read_text(encoding="utf-8"),
                previous_catalog,
            )
            self.assertEqual(
                manifest.read_text(encoding="utf-8"),
                "previous manifest\n",
            )

    def test_manifest_publish_failure_restores_previous_catalogs(self) -> None:
        """The old catalog directory must match an unchanged old manifest."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staged_catalogs = root / "staging" / "catalogs"
            staged_catalogs.mkdir(parents=True)
            (staged_catalogs / "en.json").write_text(
                '{"locale":"en","messages":{}}\n',
                encoding="utf-8",
            )
            output_dir = root / "public" / "i18n"
            output_dir.mkdir(parents=True)
            (output_dir / "previous.json").write_text(
                '{"locale":"previous","messages":{}}\n',
                encoding="utf-8",
            )
            staged_manifest = root / "staged-generated.ts"
            staged_manifest.write_text("new manifest\n", encoding="utf-8")
            manifest = root / "src" / "i18n" / "generated.ts"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("previous manifest\n", encoding="utf-8")
            original_replace = Path.replace

            def fail_manifest_replace(source: Path, target: Path) -> Path:
                """Simulate only the final manifest replacement failing."""
                if source == staged_manifest:
                    raise OSError("manifest publish failed")
                return original_replace(source, target)

            with (
                mock.patch.object(Path, "replace", new=fail_manifest_replace),
                self.assertRaisesRegex(OSError, "manifest publish failed"),
            ):
                compile_js_messages.publish_staged_i18n(
                    staged_catalogs,
                    output_dir,
                    staged_manifest,
                    manifest,
                )

            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"previous.json"},
            )
            self.assertEqual(
                manifest.read_text(encoding="utf-8"),
                "previous manifest\n",
            )

    def test_partial_language_cli_option_is_not_supported(self) -> None:
        """Runtime assets cannot be published for only part of the registry."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings_file = root / "settings.yaml"
            write_settings(settings_file)
            stderr = io.StringIO()

            with (
                self.assertRaises(SystemExit) as raised,
                mock.patch(
                    "sys.argv",
                    [
                        "compile_js_messages.py",
                        "--settings-file",
                        str(settings_file),
                        "--languages",
                        "en",
                    ],
                ),
                redirect_stderr(stderr),
            ):
                compile_js_messages.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --languages en", stderr.getvalue())

    def test_main_refuses_missing_settings_file(self) -> None:
        """A missing settings file must fail instead of inventing defaults."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            missing_settings = root / "missing-settings.yaml"
            stderr = io.StringIO()

            with (
                self.assertRaises(SystemExit) as raised,
                mock.patch(
                    "sys.argv",
                    [
                        "compile_js_messages.py",
                        "--settings-file",
                        str(missing_settings),
                        "--output-dir",
                        str(root / "public" / "i18n"),
                        "--languages-output",
                        str(root / "generated.ts"),
                    ],
                ),
                redirect_stderr(stderr),
            ):
                compile_js_messages.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("settings 文件不存在", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
