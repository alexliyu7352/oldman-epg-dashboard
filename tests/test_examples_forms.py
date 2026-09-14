"""Dashboard Form 示例的真实表单与路由测试。"""

from __future__ import annotations

import asyncio
import unittest

from apps.examples.forms import StreamProfileForm
from apps.examples.models import ExampleStreamProfile


class ExampleFormTests(unittest.TestCase):
    """验证 Form 示例不是静态占位。"""

    def test_stream_profile_form_rejects_duplicates_and_flattens_child_errors(self) -> None:
        """重复源进入顶部 message，单项 URL 错误使用真实带前缀字段名。"""
        duplicate = StreamProfileForm(
            data={
                "edit-name": "Profile",
                "edit-status": "active",
                "edit-sources_json-0": "https://one.example.test/live.m3u8",
                "edit-sources_json-1": "https://one.example.test/live.m3u8",
            },
            prefix="edit",
        )
        self.assertFalse(asyncio.run(duplicate.validate()))
        self.assertIsNotNone(duplicate.error_message)
        self.assertEqual({}, duplicate.errors)

        invalid = StreamProfileForm(
            data={
                "edit-name": "Profile",
                "edit-status": "active",
                "edit-sources_json-0": "not-a-url",
            },
            prefix="edit",
        )
        self.assertFalse(asyncio.run(invalid.validate()))
        self.assertEqual(["edit-sources_json-0"], list(invalid.errors))

    def test_stream_profile_form_preserves_fixture_json_on_edit(self) -> None:
        """真实模型 Text 能直接进入可编辑行，而不是显示原始 JSON 字符串。"""
        profile = ExampleStreamProfile(
            name="Fixture profile",
            status="active",
            sources_json='["https://one.example.test/live.m3u8","https://two.example.test/live.m3u8"]',
        )
        form = StreamProfileForm(instance=profile, prefix="edit")

        self.assertEqual(
            ["https://one.example.test/live.m3u8", "https://two.example.test/live.m3u8"],
            form.sources_json.data,
        )
        html = str(asyncio.run(form.render(action="/examples/forms/json-list/1/edit", form_mode="json")))
        self.assertIn('data-om-component="form-repeater"', html)
        self.assertIn('name="edit-sources_json-1"', html)


if __name__ == "__main__":
    unittest.main()
