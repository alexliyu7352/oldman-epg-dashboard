"""The typed Session the service uses (SESSION_MODEL): this project's, not the skeleton's."""

from __future__ import annotations

import unittest

from oldman.web.auth import session_data_for_user

from apps.accounts.models import User
from apps.examples.session import DashboardSessionData


class DashboardSessionDataTest(unittest.TestCase):
    def test_session_data_is_typed_and_contains_identity_permissions(self) -> None:
        """登录 session 必须保存完整身份字段并保持 MessagePack 类型。"""
        user = User(id=3, username="alice", display_name="Alice", email="alice@example.test", password_hash="", is_active=True, is_staff=True, is_superuser=False)

        # login_user() builds the session this way from the class the middleware attached to the request.
        value = session_data_for_user(DashboardSessionData, user, expiry=600, login_ip="127.0.0.1")
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


if __name__ == "__main__":
    unittest.main()
