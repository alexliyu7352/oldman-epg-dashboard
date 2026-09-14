#!/usr/bin/env python3
"""Build EPG browser catalogs from the unified ``messages`` translations."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from oldman.conf.schemas import I18nConfig, StaticConfig
from oldman.i18n import LanguageRegistry, language_code_variants
from oldman.i18n.frontend import compile_project_frontend_catalog
from oldman.web.i18n.assets import direct_flag_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _language_mapping(
    languages: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Convert the serializable language list into core registry input."""
    return {str(language["code"]): language for language in languages}


def read_i18n_languages(
    settings_file: Path,
) -> tuple[str, list[dict[str, object]]]:
    """Read the canonical frontend language contract from project settings."""
    default_language, languages, _ = read_i18n_contract(settings_file)
    return default_language, languages


def read_i18n_contract(
    settings_file: Path,
) -> tuple[str, list[dict[str, object]], str]:
    """Read one consistent language and static-URL settings snapshot."""
    data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
    i18n = I18nConfig.model_validate(data.get("i18n") or {})
    web = data.get("web") or {}
    static = StaticConfig.model_validate(web.get("static") or {})
    registry = LanguageRegistry(i18n.languages)
    if not registry:
        raise ValueError(f"{settings_file} 中的 i18n.languages 不能为空")
    languages = [
        {
            "aliases": list(definition.aliases),
            "babel_locale": definition.babel_locale,
            "code": definition.code,
            "flag": definition.flag,
            "locale": definition.code,
            "name": definition.name,
        }
        for definition in registry
    ]
    default_language = registry.resolve(i18n.default_language)
    if not default_language:
        raise ValueError(
            f"{settings_file} 中的 i18n.default_language 不属于 i18n.languages"
        )
    return default_language, languages, static.url


def build_language_aliases(
    languages: list[dict[str, object]],
) -> dict[str, str]:
    """Generate canonical browser aliases from the settings registry."""
    aliases: dict[str, str] = {}
    registry = LanguageRegistry(_language_mapping(languages))
    for definition in registry:
        for candidate in (definition.code, *definition.aliases):
            for variant in language_code_variants(candidate):
                aliases[variant] = definition.code
    return aliases


def js_string(value: str) -> str:
    """Serialize one Python string as a readable JavaScript literal."""
    return json.dumps(value, ensure_ascii=False)


def catalog_filename(language_code: str) -> str:
    """Convert one canonical language code into its catalog filename."""
    return language_code.lower()


def write_language_manifest(
    output_file: Path,
    settings_file: Path,
) -> None:
    """Generate the browser language index without duplicating locale identity."""
    default_language, languages, static_url = read_i18n_contract(settings_file)
    write_language_manifest_data(
        output_file,
        default_language,
        languages,
        static_url=static_url,
    )


def write_language_manifest_data(
    output_file: Path,
    default_language: str,
    languages: list[dict[str, object]],
    *,
    static_url: str,
) -> None:
    """Write a manifest from the same validated settings snapshot as catalogs."""
    aliases = build_language_aliases(languages)

    lines = [
        "export interface LanguageDefinition {",
        "  readonly code: string;",
        "  readonly locale: string;",
        "  readonly aliases: readonly string[];",
        "  readonly flag: string;",
        "  readonly flagUrl?: string;",
        "  readonly catalogPath: string;",
        "  readonly name: string;",
        "}",
        "",
        f"export const defaultLanguage = {js_string(default_language)};",
        "",
        "export const languageDefinitions = [",
    ]
    for language in languages:
        code = str(language["code"])
        raw_aliases = language["aliases"]
        language_aliases = (
            [str(alias) for alias in raw_aliases]
            if isinstance(raw_aliases, list)
            else []
        )
        flag = str(language["flag"])
        flag_url = direct_flag_url(flag, static_url=static_url)
        lines.extend(
            [
                "  {",
                f"    code: {js_string(code)},",
                f"    locale: {js_string(code)},",
                f"    aliases: {json.dumps(language_aliases, ensure_ascii=False)},",
                f"    flag: {js_string(flag)},",
            ]
        )
        lines.append(f"    flagUrl: {js_string(flag_url)},")
        lines.extend(
            [
                f"    catalogPath: {js_string('i18n/' + catalog_filename(code) + '.json')},",
                f"    name: {js_string(str(language['name']))},",
                "  },",
            ]
        )
    lines.extend(
        [
            "] as const satisfies readonly LanguageDefinition[];",
            "",
            "export const supportedLanguageCodes = languageDefinitions.map((language) => language.code);",
            "",
            "export const languageAliases: Record<string, string> = {",
        ]
    )
    for alias, code in sorted(aliases.items()):
        lines.append(f"  {js_string(alias)}: {js_string(code)},")
    lines.extend(
        [
            "};",
            "",
            "export const localeToLanguageCode: Record<string, string> = {",
        ]
    )
    for language in languages:
        code = str(language["code"])
        for variant in language_code_variants(code):
            lines.append(f"  {js_string(variant)}: {js_string(code)},")
    lines.extend(["};", ""])
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(lines), encoding="utf-8")


def write_catalog_json(
    output_file: Path,
    catalog: Mapping[str, object],
) -> None:
    """Write one validated browser TranslationCatalog."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(dict(catalog), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_language_catalogs(
    output_dir: Path,
    locales_dir: Path,
    configured_languages: list[dict[str, object]],
    *,
    compiler_command: Sequence[str] | None = None,
    project_root: Path = Path.cwd(),
) -> None:
    """Compile the complete configured set from unified ``messages.po`` files."""
    for definition in configured_languages:
        language_code = str(definition["code"])
        babel_locale = str(definition["babel_locale"])
        po_file = (
            locales_dir
            / babel_locale
            / "LC_MESSAGES"
            / "messages.po"
        )
        if po_file.exists():
            catalog = compile_project_frontend_catalog(
                project_root,
                po_file,
                fallback_locale=language_code,
                compiler_command=compiler_command,
            )
            catalog["locale"] = language_code
        else:
            catalog = {"locale": language_code, "messages": {}}
        write_catalog_json(
            output_dir / f"{catalog_filename(language_code)}.json",
            catalog,
        )


def validate_catalog_directory(
    output_dir: Path,
    configured_languages: list[dict[str, object]],
) -> None:
    """Ensure staging contains exactly one valid catalog per configured language."""
    expected = {
        f"{catalog_filename(str(language['code']))}.json"
        for language in configured_languages
    }
    actual = {path.name for path in output_dir.glob("*.json")}
    if actual != expected:
        raise RuntimeError(
            "前端 catalog 集合不完整: "
            f"expected={sorted(expected)!r}, actual={sorted(actual)!r}"
        )

    for catalog_file in sorted(output_dir.glob("*.json")):
        try:
            payload = json.loads(catalog_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{catalog_file} 不是有效 JSON") from exc
        _validated_catalog(payload, catalog_file)


def publish_staged_i18n(
    staged_catalogs: Path,
    output_dir: Path,
    staged_manifest: Path,
    languages_output: Path,
) -> None:
    """Replace the catalog set first and publish its manifest last."""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    languages_output.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = staged_catalogs.parent / "previous-catalogs"
    had_previous_catalogs = output_dir.exists()
    if had_previous_catalogs and not output_dir.is_dir():
        raise RuntimeError(f"catalog 输出路径不是目录: {output_dir}")

    if had_previous_catalogs:
        output_dir.replace(backup_dir)
    try:
        staged_catalogs.replace(output_dir)
        staged_manifest.replace(languages_output)
    except Exception:
        # The old manifest remains until its atomic replace succeeds. Restore the
        # matching old catalog directory if the final publish step fails.
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if had_previous_catalogs and backup_dir.exists():
            backup_dir.replace(output_dir)
        raise


def compile_frontend_i18n(
    output_dir: Path,
    locales_dir: Path,
    settings_file: Path,
    languages_output: Path,
    *,
    compiler_command: Sequence[str] | None = None,
    project_root: Path = Path.cwd(),
) -> None:
    """Stage, validate, and atomically publish one complete browser i18n set."""
    default_language, configured_languages, static_url = read_i18n_contract(
        settings_file
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    languages_output.parent.mkdir(parents=True, exist_ok=True)

    with (
        TemporaryDirectory(
            prefix=".oldman-i18n-catalogs-",
            dir=output_dir.parent,
        ) as catalog_temporary_directory,
        TemporaryDirectory(
            prefix=".oldman-i18n-manifest-",
            dir=languages_output.parent,
        ) as manifest_temporary_directory,
    ):
        staged_catalogs = Path(catalog_temporary_directory) / "catalogs"
        staged_manifest = (
            Path(manifest_temporary_directory) / languages_output.name
        )
        write_language_catalogs(
            staged_catalogs,
            locales_dir,
            configured_languages,
            compiler_command=compiler_command,
            project_root=project_root,
        )
        validate_catalog_directory(staged_catalogs, configured_languages)
        write_language_manifest_data(
            staged_manifest,
            default_language,
            configured_languages,
            static_url=static_url,
        )
        publish_staged_i18n(
            staged_catalogs,
            output_dir,
            staged_manifest,
            languages_output,
        )


def main() -> int:
    """Compile frontend catalogs and regenerate the language index."""
    parser = argparse.ArgumentParser(
        description="编译前端 PO 文件为 JSON 语言包",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--locales-dir",
        default=str(PROJECT_ROOT / "locales"),
        help="语言目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "frontend" / "public" / "i18n"),
        help="输出 JSON 语言包目录",
    )
    parser.add_argument(
        "--settings-file",
        default=str(PROJECT_ROOT / "data" / "web_settings.yaml"),
        help="项目配置文件",
    )
    parser.add_argument(
        "--languages-output",
        default=str(PROJECT_ROOT / "frontend" / "src" / "i18n" / "generated.ts"),
        help="前端语言索引输出路径",
    )
    args = parser.parse_args()

    locales_dir = Path(args.locales_dir)
    settings_file = Path(args.settings_file)
    if not settings_file.exists():
        parser.error(
            f"settings 文件不存在: {settings_file}。请先生成或提供 --settings-file。"
        )

    compile_frontend_i18n(
        Path(args.output_dir),
        locales_dir,
        settings_file,
        Path(args.languages_output),
        project_root=PROJECT_ROOT,
    )
    return 0


def _validated_catalog(
    payload: object,
    source: Path,
) -> dict[str, object]:
    """Reject malformed compiler output before writing browser assets."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"oldman-web-i18n 为 {source} 返回的 catalog 不是对象")
    locale = payload.get("locale")
    messages = payload.get("messages")
    if not isinstance(locale, str) or not locale:
        raise RuntimeError(f"oldman-web-i18n 为 {source} 返回了无效 locale")
    if not isinstance(messages, dict):
        raise RuntimeError(f"oldman-web-i18n 为 {source} 返回了无效 messages")
    for message_id, value in messages.items():
        if not isinstance(message_id, str) or not isinstance(
            value,
            (str, list),
        ):
            raise RuntimeError(
                f"oldman-web-i18n 为 {source} 返回了无效消息"
            )
        if isinstance(value, list) and not all(
            isinstance(item, str) for item in value
        ):
            raise RuntimeError(
                f"oldman-web-i18n 为 {source} 返回了无效复数消息"
            )
    plural_rule = payload.get("pluralRule")
    if plural_rule is not None and not isinstance(plural_rule, str):
        raise RuntimeError(
            f"oldman-web-i18n 为 {source} 返回了无效 pluralRule"
        )
    return {
        "locale": locale,
        "messages": messages,
        **(
            {"pluralRule": plural_rule}
            if isinstance(plural_rule, str) and plural_rule
            else {}
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
