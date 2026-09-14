"""后台用户管理页面契约测试。"""

from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from apps.auth.models import OldmanUser
from sqlalchemy import Table, select

from oldman.conf.schemas import DatabaseConfig
from oldman.db import DatabaseManager
from oldman.web.components.tables.views import TableValidationError

ROOT = Path(__file__).resolve().parents[1]


class AuthUserFormTest(unittest.TestCase):
    """验证 OldmanUser 后台管理表单。"""

    def test_user_model_has_staff_field_and_superuser_staff_constraint(self) -> None:
        """OldmanUser 必须用 is_staff 表示后台访问权限，并约束超级用户同时是 staff。"""
        table = cast(Table, OldmanUser.__table__)
        columns = table.columns
        constraints = {constraint.name for constraint in table.constraints}

        self.assertIn("is_staff", columns)
        self.assertFalse(columns["is_staff"].nullable)
        self.assertIn("ck_oldman_user_superuser_is_staff", constraints)

    def test_user_create_form_hashes_password_and_never_exposes_password_hash(self) -> None:
        """创建用户表单必须写入 hash，且不能把 password_hash 暴露为可编辑字段。"""
        from apps.auth.forms import UserCreateForm

        form = UserCreateForm(
            data={
                "username": "alice",
                "email": "alice@example.test",
                "display_name": "Alice",
                "password": "Str0ngPass!2026",
                "confirm_password": "Str0ngPass!2026",
                "is_active": "y",
                "is_staff": "y",
                "is_superuser": "",
            }
        )

        self.assertTrue(asyncio.run(form.validate()))
        user = asyncio.run(form.save_user())

        self.assertNotEqual(user.password_hash, "Str0ngPass!2026")
        self.assertTrue(user.check_password("Str0ngPass!2026"))
        self.assertNotIn("password_hash", form._fields)

    def test_user_form_rejects_duplicate_username_and_email(self) -> None:
        """创建/编辑用户必须提前校验唯一字段，不能把唯一索引异常暴露成 500。"""
        from apps.auth.forms import UserCreateForm

        existing = OldmanUser(id=12, username="alice", email="alice@example.test", password_hash="", is_active=True, is_superuser=False)
        session = FakeUserLookupSession([existing, existing])
        form = UserCreateForm(
            data={
                "username": "alice",
                "email": "alice@example.test",
                "password": "Str0ngPass!2026",
                "confirm_password": "Str0ngPass!2026",
                "is_staff": "y",
            },
            session=session,
        )

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("username", form.errors)
        self.assertIn("email", form.errors)

    def test_user_edit_form_rejects_disabling_current_user(self) -> None:
        """编辑页也必须拒绝当前用户禁用自己，不能只保护 status modal。"""
        from apps.auth.forms import UserEditForm

        user = OldmanUser(id=7, username="root", email="root@example.test", password_hash="", is_active=True, is_superuser=True)
        form = UserEditForm(
            data={
                "username": "root",
                "email": "root@example.test",
                "display_name": "Root",
                "is_active": "",
                "is_staff": "y",
                "is_superuser": "y",
            },
            instance=user,
            session=FakeUserLookupSession({}),
        )
        form.current_user_id = 7

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("is_active", form.errors)

    def test_user_edit_form_rejects_current_user_staff_removal(self) -> None:
        """当前后台用户不能通过编辑页移除自己的 staff 权限。"""
        from apps.auth.forms import UserEditForm

        user = OldmanUser(id=7, username="root", email="root@example.test", password_hash="", is_active=True, is_staff=True, is_superuser=True)
        form = UserEditForm(
            data={
                "username": "root",
                "email": "root@example.test",
                "display_name": "Root",
                "is_active": "y",
                "is_staff": "",
                "is_superuser": "y",
            },
            instance=user,
            session=FakeUserLookupSession({}),
        )
        form.current_user_id = 7

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("is_staff", form.errors)

    def test_user_edit_form_rejects_current_user_superuser_removal(self) -> None:
        """当前超级用户不能通过编辑页把自己移出后台权限。"""
        from apps.auth.forms import UserEditForm

        user = OldmanUser(id=7, username="root", email="root@example.test", password_hash="", is_active=True, is_staff=True, is_superuser=True)
        form = UserEditForm(
            data={
                "username": "root",
                "email": "root@example.test",
                "display_name": "Root",
                "is_active": "y",
                "is_staff": "y",
                "is_superuser": "",
            },
            instance=user,
            session=FakeUserLookupSession({}),
        )
        form.current_user_id = 7

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("is_superuser", form.errors)

    def test_user_password_form_requires_matching_confirmation(self) -> None:
        """修改密码表单必须校验确认密码一致。"""
        from oldman.web.auth import UserPasswordForm

        form = UserPasswordForm(data={"password": "Str0ngPass!2026", "confirm_password": "different"})

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("confirm_password", form.errors)

    def test_user_password_form_requires_letters_and_numbers(self) -> None:
        """密码策略必须在 WTForms 和 HTML 属性两层表达。"""
        from oldman.web.auth import UserPasswordForm

        form = UserPasswordForm(data={"password": "abcdefgh", "confirm_password": "abcdefgh"})

        self.assertFalse(asyncio.run(form.validate()))
        self.assertIn("password", form.errors)
        self.assertEqual(form.password.render_kw["minlength"], 8)
        self.assertIn("pattern", form.password.render_kw)

    def test_user_filter_form_outputs_status_and_last_login_filters(self) -> None:
        """用户列表筛选表单必须覆盖启停、staff、超级用户和最近登录时间范围。"""
        from apps.auth.forms import UserFilterForm

        html = str(asyncio.run(UserFilterForm().render(method="get", table_target="#users-table")))

        self.assertIn('data-om-component="table-filter-form"', html)
        self.assertIn('name="is_active"', html)
        self.assertIn('name="is_staff"', html)
        self.assertIn('name="is_superuser"', html)
        self.assertIn('name="last_login_from"', html)
        self.assertIn('name="last_login_to"', html)
        self.assertIn('data-om-component="date-time-picker"', html)


class AuthUserServiceTest(unittest.TestCase):
    """验证用户管理服务的安全边界。"""

    def test_session_data_is_typed_and_contains_identity_permissions(self) -> None:
        """登录 session 必须保存完整身份字段并保持 MessagePack 类型。"""
        from apps.auth.services import session_data
        from apps.auth.session import DashboardSessionData

        user = OldmanUser(id=3, username="alice", display_name="Alice", email="alice@example.test", password_hash="", is_active=True, is_staff=True, is_superuser=False)

        value = session_data(user, "127.0.0.1")
        restored = DashboardSessionData.from_msgpack(value.to_msgpack())

        self.assertIsInstance(value, DashboardSessionData)
        self.assertEqual(restored, value)
        self.assertEqual(value.user_id, 3)
        self.assertEqual(value.username, "alice")
        self.assertEqual(value.display_name, "Alice")
        self.assertEqual(value.login_ip, "127.0.0.1")
        self.assertGreater(value.login_time, 0)
        self.assertIs(value.is_active, True)
        self.assertIs(value.is_staff, True)
        self.assertIs(value.is_superuser, False)
        self.assertEqual(DashboardSessionData.__annotations__, {})
        self.assertNotIn("ip", DashboardSessionData.__struct_fields__)

    def test_authenticate_user_rejects_non_staff_user_before_password_success(self) -> None:
        """非 staff 用户即使密码正确也不能登录后台。"""
        from apps.auth import services

        async def run_case() -> None:
            user = OldmanUser(id=4, username="viewer", email="viewer@example.test", password_hash="", is_active=True, is_staff=False, is_superuser=False)
            user.set_password("Str0ngPass!2026")
            original_get_user = services.get_user_by_username

            async def fake_get_user(username: str):
                self.assertEqual(username, "viewer")
                return user

            services.get_user_by_username = fake_get_user
            try:
                self.assertIsNone(await services.authenticate_user("viewer", "Str0ngPass!2026"))
            finally:
                services.get_user_by_username = original_get_user

        asyncio.run(run_case())

    def test_ensure_default_admin_sets_staff_and_superuser(self) -> None:
        """默认管理员必须同时是 active、staff 和 superuser。"""
        source = (ROOT / "apps" / "auth" / "services.py").read_text(encoding="utf-8")

        self.assertIn("is_active=True", source)
        self.assertIn("is_staff=True", source)
        self.assertIn("is_superuser=True", source)

    def test_superuser_is_normalized_to_staff_on_real_db_flush(self) -> None:
        """项目实际 db_manager.get_session() flush 路径必须触发 SQLAlchemy before_flush 归一化。"""
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                db_url = f"sqlite+aiosqlite:///{Path(tmp) / 'auth-user-test.db'}"
                username = "staff-normalize-test"
                manager = DatabaseManager(DatabaseConfig(url=db_url))
                try:
                    await manager.create_db_and_tables()
                    async with manager.get_session() as session:
                        result = await session.execute(select(OldmanUser).where(OldmanUser.username == username))
                        existing = result.scalar_one_or_none()
                        if existing is not None:
                            await session.delete(existing)
                            await session.flush()

                        user = OldmanUser(username=username, email=None, display_name=username, password_hash="", is_active=True, is_staff=False, is_superuser=True)
                        user.set_password("Str0ngPass!2026")
                        session.add(user)
                        await session.flush()
                        self.assertTrue(user.is_staff)
                        await session.delete(user)
                finally:
                    await manager.close()

        asyncio.run(run_case())

    def test_set_user_active_rejects_disabling_current_user(self) -> None:
        """服务端必须拒绝禁用当前登录用户。"""
        from apps.auth.services import UserManagementError, set_user_active

        user = OldmanUser(id=7, username="alice", email="alice@example.test", password_hash="", is_active=True, is_superuser=False)

        with self.assertRaises(UserManagementError):
            set_user_active(user, False, current_user_id=7)

    def test_delete_user_rejects_superuser(self) -> None:
        """删除超级用户必须被服务端拒绝。"""
        from apps.auth.services import UserManagementError, validate_user_delete

        user = OldmanUser(id=8, username="root", email="root@example.test", password_hash="", is_active=True, is_superuser=True)

        with self.assertRaises(UserManagementError):
            validate_user_delete(user, current_user_id=1)

    def test_change_user_password_replaces_existing_hash(self) -> None:
        """修改密码后旧密码不能再通过校验，新密码必须可用。"""
        from apps.auth.services import change_user_password

        user = OldmanUser(id=9, username="bob", email="bob@example.test", password_hash="", is_active=True, is_superuser=False)
        user.set_password("OldPass!2026")
        old_hash = user.password_hash

        change_user_password(user, "NewPass!2026")

        self.assertNotEqual(user.password_hash, old_hash)
        self.assertFalse(user.check_password("OldPass!2026"))
        self.assertTrue(user.check_password("NewPass!2026"))


class AuthUserTableTest(unittest.TestCase):
    """验证后台用户表格和操作入口。"""

    def test_user_table_contract(self) -> None:
        """用户表格必须覆盖状态、staff、超级用户、最近登录和行级操作。"""
        from apps.auth.tables import UserTable

        columns = [
            (str(label), *definition)
            for label, *definition in UserTable.columns
        ]
        self.assertEqual(UserTable.route_path, "/users/table")
        self.assertTrue(UserTable.selectable)
        self.assertIn("username", UserTable.search_fields)
        self.assertIn("email", UserTable.search_fields)
        self.assertIn("display_name", UserTable.search_fields)
        self.assertIn(("Status", "is_active", "get_column_is_active_data"), columns)
        self.assertIn(("Staff", "is_staff", "get_column_is_staff_data"), columns)
        self.assertIn(("Superuser", "is_superuser", "get_column_is_superuser_data"), columns)
        self.assertIn(("Last Login", "last_login_at", "get_column_last_login_at_data"), columns)
        self.assertIn(("Action", None, "get_column_action_data"), columns)

    def test_user_table_boolean_filter_rejects_invalid_value(self) -> None:
        """用户布尔筛选非法值必须返回表格校验错误。"""
        from apps.auth.tables import UserTable

        table = UserTable()
        request = table.build_table_request(make_request(args={"filter.is_active": "maybe"}), route_kwargs={})

        with self.assertRaises(TableValidationError):
            asyncio.run(table.filter_is_active(select(OldmanUser), "maybe", request))

    def test_user_table_filters_staff_status(self) -> None:
        """用户表格必须能按 staff 权限筛选。"""
        from apps.auth.tables import UserTable

        table = UserTable()
        request = table.build_table_request(make_request(args={"filter.is_staff": "true"}), route_kwargs={})
        query = asyncio.run(table.filter_is_staff(select(OldmanUser), "true", request))

        self.assertIn("oldman_user.is_staff IS true", str(query))

    def test_user_table_action_exposes_password_disable_and_delete_modals(self) -> None:
        """行级操作必须暴露密码、禁用和删除 modal，而不是跳转到临时 dashboard 弹窗。"""
        from apps.auth.tables import UserTable

        user = SimpleNamespace(id=12, username="alice", is_active=True, is_superuser=False, last_login_at=dt.datetime(2026, 6, 11, 8, 0))

        html, raw_value = UserTable().get_column_action_data(cast(Any, user))

        self.assertEqual(raw_value, "")
        html_text = str(html)
        self.assertIn('data-om-modal-target="#user-password-modal"', html_text)
        self.assertIn('data-om-modal-url="/users/12/password-modal"', html_text)
        self.assertIn('data-om-modal-target="#user-status-modal"', html_text)
        self.assertIn('data-om-modal-url="/users/12/status-modal"', html_text)
        self.assertIn('data-om-modal-target="#user-delete-modal"', html_text)
        self.assertIn('data-om-modal-url="/users/12/delete-modal"', html_text)

    def test_user_remote_modal_titles_escape_dynamic_username_html(self) -> None:
        """用户远程 modal 标题允许 HTML，但 username 动态值必须 escape 后再拼入。"""
        source = (ROOT / "apps" / "auth" / "views.py").read_text(encoding="utf-8")

        self.assertIn("from markupsafe import escape", source)
        self.assertIn("render_session_password_modal(", source)
        for route_name in (
            'name="users_password_modal"',
            'name="users_status_modal"',
            'name="users_delete_modal"',
        ):
            route_source = source.split(route_name, 1)[1].split("@app.", 1)[0]
            self.assertIn("escape(user.username)", route_source)


if __name__ == "__main__":
    unittest.main()


class FakeScalarResult:
    """模拟 SQLAlchemy result.scalar_one_or_none。"""

    def __init__(self, value):
        """保存要返回的查询结果。"""
        self.value = value

    def scalar_one_or_none(self):
        """返回单条伪查询结果。"""
        return self.value


class FakeUserLookupSession:
    """按 SQL 文本关键字返回用户记录的轻量假 session。"""

    def __init__(self, rows):
        """保存按调用顺序返回的查询结果。"""
        self.rows = list(rows)

    async def execute(self, statement):
        """按表单字段校验顺序返回对应伪结果。"""
        del statement
        if self.rows:
            return FakeScalarResult(self.rows.pop(0))
        return FakeScalarResult(None)


def make_request(*, args: dict[str, str] | None = None, headers: dict[str, str] | None = None):
    """构造表格测试所需的轻量请求对象。"""

    class Args(dict):
        """提供 Sanic request.args 兼容的 getlist。"""

        def getlist(self, key: str):
            value = self.get(key)
            if value is None:
                return []
            if isinstance(value, list):
                return value
            return [value]

    return SimpleNamespace(args=Args(args or {}), headers=headers or {}, path="/users/table", query_string="", ctx=SimpleNamespace(session=None))
