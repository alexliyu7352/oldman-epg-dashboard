"""后台权限装饰器。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from apps.auth.session import dashboard_session
from oldman.web.http import authentication_required_response, permission_denied_response, resolve_response_mode
from oldman.web.request import Request

Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])


def admin_required() -> Callable[[Handler], Handler]:
    """要求当前请求已经登录后台。"""

    def decorator(handler: Handler) -> Handler:
        """包装 Sanic 视图函数，未登录时跳转到登录页。"""

        @wraps(handler)
        async def wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
            """检查 request.ctx.session 中的登录状态。"""
            session = dashboard_session(request)
            response_mode = resolve_response_mode(request)
            if not session.is_authenticated():
                return authentication_required_response(request, response_mode)

            if not session.is_staff:
                return await permission_denied_response(request, response_mode)

            return await handler(request, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
