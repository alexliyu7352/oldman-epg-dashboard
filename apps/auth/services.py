"""后台认证服务。"""

from __future__ import annotations

from typing import cast

from oldman.auth import has_staff_access
from oldman.web.auth import authenticate_credentials
from oldman.web.request import Request

from apps.auth.models import OldmanUser


async def authenticate_user(request: Request, username: str, password: str) -> OldmanUser | None:
    """校验用户名密码并返回可登录的后台用户：只有活跃的 staff 才能进后台。

    凭据经由框架配置的登录后端核对(默认是用户表),本项目只在其上加 staff 这一条策略。
    """
    user = await authenticate_credentials(request, username=username, password=password)
    if user is None or not has_staff_access(user):
        return None
    return cast(OldmanUser, user)
