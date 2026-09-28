"""后台用户管理表格：列、badge、行菜单和筛选都来自框架，这里只接本站的路由。"""

from __future__ import annotations

from typing import Any

from oldman.web.auth import UserTable as SharedUserTable

from apps.auth.models import OldmanUser


class UserTable(SharedUserTable):
    """后台用户管理表格 data endpoint。"""

    route_name = "users_table"
    route_path = "/users/table"
    model = OldmanUser

    def object_url(self, row: Any, action: str) -> str:
        """把 edit / password-modal / status-modal / delete-modal 映射到 /users/<id>/… 路由。"""
        return f"/users/{row.id}/{action}"


__all__ = ["UserTable"]
