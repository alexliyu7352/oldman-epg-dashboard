"""Dashboard 远程数据输入示例的核心行为测试。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from markupsafe import Markup

from apps.examples.models import ExampleLogo
from oldman.web.components.selects import SelectContext


class ExampleDataInputTests(unittest.TestCase):
    """验证示例复用真实 provider，并在保存前重新检查 Logo。"""

    def test_logo_provider_returns_trusted_rich_choice_and_filters_country(self) -> None:
        """富候选保留纯文本名称，依赖国家进入数据库查询。"""
        from apps.examples.providers import ExampleLogoProvider

        logo = ExampleLogo(
            id=7,
            name="Horizon US",
            slug="horizon-us",
            country_code="US",
            svg_path="/static/examples/logos/horizon.svg",
            is_available=True,
        )
        provider = ExampleLogoProvider()
        choice = provider.get_option(logo)

        self.assertEqual(7, choice.id)
        self.assertEqual("Horizon US · US", choice.text)
        self.assertIsInstance(choice.html, Markup)
        self.assertIn('width="36"', str(choice.html))
        self.assertIn('loading="lazy"', str(choice.html))

        query = asyncio.run(
            provider.filter_queryset(
                SimpleNamespace(),
                SimpleNamespace(),
                asyncio.run(provider.get_queryset(SimpleNamespace(), SimpleNamespace())),
                "",
                {"country_code": "US"},
            )
        )
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("example_logo.country_code = 'US'", compiled)
        self.assertIn("example_logo.is_available IS true", compiled)

    def test_remote_logo_form_rejects_missing_or_wrong_country_logo(self) -> None:
        """签名 provider 只负责候选，提交值仍由 Form 对数据库重验。"""
        from apps.examples.forms import LogoSelectForm

        logo = ExampleLogo(
            id=1,
            name="Atlas US",
            slug="atlas-us",
            country_code="US",
            svg_path="/static/examples/logos/atlas.svg",
            is_available=True,
        )
        session = SimpleNamespace(get=AsyncMock(return_value=logo))
        valid = LogoSelectForm(data={"country_code": "US", "logo_id": "1"}, session=session)
        self.assertTrue(asyncio.run(valid.validate()))
        self.assertEqual(1, valid.cleaned_data["logo_id"])

        wrong_country = LogoSelectForm(data={"country_code": "GB", "logo_id": "1"}, session=session)
        self.assertFalse(asyncio.run(wrong_country.validate()))
        self.assertIn("logo_id", wrong_country.errors)

        session.get.return_value = None
        missing = LogoSelectForm(data={"country_code": "US", "logo_id": "999"}, session=session)
        self.assertFalse(asyncio.run(missing.validate()))
        self.assertIn("logo_id", missing.errors)

    def test_logo_autocomplete_rejects_non_numeric_hidden_value(self) -> None:
        """伪造的隐藏 ID 应成为字段错误，而不是让请求变成 500。"""
        from apps.examples.forms import LogoAutocompleteForm

        form = LogoAutocompleteForm(
            data={"country_code": "US", "logo_id": "not-an-id"},
            session=SimpleNamespace(get=AsyncMock(return_value=None)),
        )

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("logo_id", form.errors)

    def test_tag_provider_searches_and_paginates_fixture_rows(self) -> None:
        """标签 Provider 直接读取现有 ExampleTag fixture 并遵守六条分页。"""
        from apps.examples.providers import ExampleTagProvider

        provider = ExampleTagProvider()
        context = SelectContext(
            provider="example_tags",
            field_name="tag_ids",
            multiple=True,
            dependent_fields=(),
            page_size=6,
            value_field="id",
            label_mode="text",
        )
        session = SimpleNamespace(is_authenticated=lambda: True, is_staff=True)

        async def query(args: dict[str, str]):
            return await provider.handle_request(
                SimpleNamespace(args=args, ctx=SimpleNamespace(session=session)),
                context=context,
            )

        first_page = asyncio.run(query({"page": "1", "page_size": "6"}))
        search = asyncio.run(query({"q": "stream"}))

        self.assertEqual(6, len(first_page["results"]))
        self.assertTrue(first_page["more"])
        self.assertEqual(["Streaming"], [item["text"] for item in search["results"]])


if __name__ == "__main__":
    unittest.main()
