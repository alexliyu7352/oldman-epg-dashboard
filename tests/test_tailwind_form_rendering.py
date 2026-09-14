"""Tailwind form rendering contract tests."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from apps.epg_admin.forms import optional_int
from wtforms import StringField

from oldman.web.components.forms import TailwindForm, TailwindTableFilterForm

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProfileForm(TailwindForm):
    """Small form used to verify Tailwind field structure."""

    name = StringField("Name", description="Visible helper")


class SearchForm(TailwindTableFilterForm):
    """Small table filter form used to verify toolbar defaults."""

    q = StringField("Search")
    status = StringField("Status")


class TailwindFormRenderingTest(unittest.TestCase):
    """Protect Tailwind form structure used by templates and CSS."""

    def test_field_markup_has_stable_tailwind_structure(self) -> None:
        html = str(asyncio.run(ProfileForm().render()))

        self.assertIn("om-form-grid", html)
        self.assertIn("om-form-field", html)
        self.assertIn("om-form-control-stack", html)
        self.assertIn('data-om-form-field-name="name"', html)
        self.assertIn('class="om-form-label"', html)
        self.assertIn("Visible helper", html)
        self.assertIn("om-form-actions om-form-actions-end", html)

    def test_table_filter_form_uses_toolbar_by_default(self) -> None:
        html = str(asyncio.run(SearchForm().render(table_target="#items-table", method="get", submit_label="Filter")))

        self.assertIn('data-om-component="table-filter-form"', html)
        self.assertIn('data-om-table-target="#items-table"', html)
        self.assertIn('class="om-filter-toolbar"', html)
        self.assertIn("om-form-grid", html)
        self.assertIn("om-form-field", html)
        self.assertIn('data-om-form-field-name="q"', html)
        self.assertIn(">Filter</button>", html)

    def test_optional_int_preserves_integer_protocol(self) -> None:
        """迁移不能把 int() 支持的对象缩窄成只能解析字符串的值。"""

        class IntegerLike:
            def __int__(self) -> int:
                return 17

        self.assertEqual(optional_int(IntegerLike()), 17)

    def test_tailwind_entry_safelists_backend_layout_spans(self) -> None:
        css_entry = (PROJECT_ROOT / "frontend/src/css/app.css").read_text()
        shared_css = (PROJECT_ROOT / "frontend/node_modules/oldman-web/dist/styles/tailwind.css").read_text()

        self.assertNotIn("@source inline", css_entry)
        self.assertIn("@source inline", shared_css)
        self.assertIn("{sm:,md:,lg:,xl:}col-span-{1,2,3,4,5,6,7,8,9,10,11,12}", shared_css)


if __name__ == "__main__":
    unittest.main()
