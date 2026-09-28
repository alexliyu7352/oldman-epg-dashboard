"""这个仓库只允许出现自己的业务逻辑，框架已经有的东西不再复制一份。"""

from __future__ import annotations

import unittest
from pathlib import Path

import oldman
from oldman.testing.duplication import (
    duplicate_functions,
    forbidden_attributes,
    forbidden_imports,
    identical_files,
)

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = Path(oldman.__file__).resolve().parent
PROJECT_SOURCES = (ROOT / "apps", ROOT / "config", ROOT / "services", ROOT / "scripts")

# 还没有搬走的重复，逐条写明原因；修掉一条就从这里删掉一条。
ALLOWED_FRAMEWORK_DUPLICATES = frozenset(
    {
        # verify-dashboard-browser.py 里带着一份旧的 CDP 客户端；这个文件正在按领域拆分，
        # 拆完后统一改用 oldman.testing 的浏览器助手。
        "recv_json",
        "configure_viewport",
        "__init__",
        "pump",
    }
)


class ProjectDoesNotCopyTheFrameworkTest(unittest.TestCase):
    def test_no_project_function_repeats_a_framework_implementation(self) -> None:
        unexpected = []
        for source in PROJECT_SOURCES:
            if not source.exists():
                continue
            for item in duplicate_functions(source, FRAMEWORK, min_lines=6):
                if item.name in ALLOWED_FRAMEWORK_DUPLICATES:
                    continue
                unexpected.append(item.describe(left_root=ROOT, right_root=FRAMEWORK.parent))

        self.assertEqual([], unexpected)

    def test_no_project_file_is_a_byte_copy_of_a_framework_file(self) -> None:
        matches = [
            item.describe(left_root=ROOT, right_root=FRAMEWORK.parent)
            for source in PROJECT_SOURCES
            if source.exists()
            for item in identical_files(source, FRAMEWORK, suffixes=(".py", ".html"))
        ]

        self.assertEqual([], matches)

    def test_project_code_uses_public_entry_points(self) -> None:
        # Admin 内部实现不是公共 API；模板环境要走 render_template/render_fragment。
        private_imports = [
            item.describe(root=ROOT)
            for source in PROJECT_SOURCES
            if source.exists()
            # install_admin is the Admin's public entry: this demo mounts the built-in Admin at /admin.
            for item in forbidden_imports(source, modules=("oldman.apps.admin",), allowed_names=("install_admin",))
        ]
        # 视图不直接摸模板环境；服务的 init() 才是安装 loader 和全局变量的地方。
        raw_environment = [
            item.describe(root=ROOT)
            for item in forbidden_attributes(ROOT / "apps", attributes=("app.ext.environment",))
        ]

        self.assertEqual([], private_imports)
        self.assertEqual([], raw_environment)


if __name__ == "__main__":
    unittest.main()
