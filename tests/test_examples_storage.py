"""Dashboard 文件上传与 Storage 示例的真实边界测试。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sanic.request import File

from apps.examples.models import ExampleAsset
from oldman.storage import InMemoryStorage


def uploaded_file(name: str, body: bytes = b"content", content_type: str = "application/octet-stream") -> File:
    """构造 Sanic 已缓存到内存的上传文件。"""
    return File(type=content_type, body=body, name=name)


class ExampleStorageTests(unittest.TestCase):
    """验证 Demo 使用现有 ModelForm 和固定前缀 Storage API。"""

    def test_asset_form_requires_document_only_when_creating(self) -> None:
        from apps.examples import forms

        self.assertTrue(hasattr(forms, "ExampleAssetForm"), "ExampleAssetForm is missing")
        ExampleAssetForm = forms.ExampleAssetForm

        missing = ExampleAssetForm(data={"display_name": "Guide", "description": ""})
        self.assertFalse(asyncio.run(missing.validate()))
        self.assertIn("document_path", missing.errors)

        existing = ExampleAsset(display_name="Guide", document_path="examples/assets/guide.txt")
        edit = ExampleAssetForm(data={"display_name": "Renamed", "description": ""}, instance=existing)
        self.assertTrue(asyncio.run(edit.validate()))
        self.assertEqual("examples/assets/guide.txt", existing.document_path)

        upload = ExampleAssetForm(
            data={"display_name": "Guide", "description": ""},
            files={
                "document_path": uploaded_file("guide.txt", b"guide", "text/plain"),
                "preview_path": uploaded_file("preview.png", b"png", "image/png"),
            },
        )
        self.assertTrue(asyncio.run(upload.validate()))

    def test_storage_api_demo_uses_fixed_names_and_cleans_every_file(self) -> None:
        from apps.examples import services

        self.assertTrue(hasattr(services, "run_storage_api_demo"), "run_storage_api_demo is missing")
        storage = InMemoryStorage(alias="default")
        registry = SimpleNamespace(using=lambda alias: storage if alias == "default" else None)
        with patch.object(services, "storages", registry):
            result = asyncio.run(services.run_storage_api_demo())

        self.assertEqual("examples/storage-api/sample.txt", result["saved_name"])
        self.assertNotEqual(result["saved_name"], result["collision_name"])
        self.assertEqual(result["saved_name"], result["overwritten_name"])
        self.assertEqual("overwritten by the Storage API demo", result["content"])
        self.assertTrue(result["exists_before_delete"])
        self.assertFalse(result["exists_after_delete"])
        self.assertFalse(asyncio.run(storage.exists(result["saved_name"])))
        self.assertFalse(asyncio.run(storage.exists(result["collision_name"])))


if __name__ == "__main__":
    unittest.main()
