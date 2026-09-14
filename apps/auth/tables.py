"""后台用户管理表格。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from markupsafe import Markup, escape
from sqlalchemy import or_, select

from apps.auth.models import OldmanUser
from apps.epg_admin.tables import badge
from oldman.i18n import gettext_lazy as _
from oldman.web.components.tables import SQLAlchemyTableView, TailwindTableRenderer
from oldman.web.components.tables.views import TableValidationError


def is_authenticated_request(request: Any) -> bool:
    """判断当前请求是否已经登录后台。"""
    session = getattr(getattr(request, "ctx", None), "session", None)
    return bool(session and session.is_authenticated())


def parse_boolean_filter(value: object) -> bool:
    """解析用户列表布尔筛选值，非法值返回表格校验错误。"""
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise TableValidationError(_("Invalid boolean filter"))


def parse_filter_datetime(value: object) -> dt.datetime | None:
    """解析用户列表日期时间筛选值。"""
    if value in {"", None}:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        raise TableValidationError(_("Invalid datetime filter")) from None


class UserTable(SQLAlchemyTableView):
    """后台用户管理表格 data endpoint。"""

    renderer_class = TailwindTableRenderer
    route_name = "users_table"
    route_path = "/users/table"
    model = OldmanUser
    page_size = 10
    selectable = True
    ordering = ["username"]
    search_fields = ["username", "email", "display_name"]
    unsortable_columns = ["action"]
    empty_message = _("No users found.")
    columns = [
        (_("Username"), "username", "get_column_username_data"),
        (_("Email"), "email"),
        (_("Display Name"), "display_name"),
        (_("Status"), "is_active", "get_column_is_active_data"),
        (_("Staff"), "is_staff", "get_column_is_staff_data"),
        (_("Superuser"), "is_superuser", "get_column_is_superuser_data"),
        (_("Last Login"), "last_login_at", "get_column_last_login_at_data"),
        (_("Action"), None, "get_column_action_data"),
    ]

    async def check_auth(self, request: Any) -> bool:
        """检查当前请求是否允许访问用户表格。"""
        return is_authenticated_request(request)

    async def get_queryset(self):
        """返回后台用户基础查询。"""
        return select(OldmanUser)

    async def apply_search(self, query, table_request):
        """按用户名、邮箱和展示名搜索后台用户。"""
        search = table_request.q.strip()
        if not search:
            return query
        like = f"%{search}%"
        return query.where(or_(OldmanUser.username.like(like), OldmanUser.email.like(like), OldmanUser.display_name.like(like)))

    async def filter_is_active(self, query, value: object, table_request):
        """按启停状态筛选用户。"""
        return query.where(OldmanUser.is_active.is_(parse_boolean_filter(value)))

    async def filter_is_staff(self, query, value: object, table_request):
        """按后台 staff 权限筛选用户。"""
        return query.where(OldmanUser.is_staff.is_(parse_boolean_filter(value)))

    async def filter_is_superuser(self, query, value: object, table_request):
        """按超级用户标记筛选用户。"""
        return query.where(OldmanUser.is_superuser.is_(parse_boolean_filter(value)))

    async def filter_last_login_from(self, query, value: object, table_request):
        """按最近登录开始时间筛选用户。"""
        parsed = parse_filter_datetime(value)
        return query.where(OldmanUser.last_login_at >= parsed) if parsed else query

    async def filter_last_login_to(self, query, value: object, table_request):
        """按最近登录结束时间筛选用户。"""
        parsed = parse_filter_datetime(value)
        return query.where(OldmanUser.last_login_at <= parsed) if parsed else query

    def get_column_username_data(self, row: OldmanUser, **kwargs: object):
        """渲染用户名列并链接到编辑页。"""
        return Markup(f'<a href="/users/{row.id}/edit" class="link-primary font-medium">{escape(row.username)}</a>'), row.username

    def get_column_is_active_data(self, row: OldmanUser, **kwargs: object):
        """渲染启停状态 badge。"""
        return badge(str(_("Active") if row.is_active else _("Disabled")), tone="success" if row.is_active else "danger"), bool(row.is_active)

    def get_column_is_staff_data(self, row: OldmanUser, **kwargs: object):
        """渲染 staff 权限 badge。"""
        return badge(str(_("Staff") if row.is_staff else _("No Staff")), tone="info" if row.is_staff else "secondary"), bool(row.is_staff)

    def get_column_is_superuser_data(self, row: OldmanUser, **kwargs: object):
        """渲染超级用户 badge。"""
        return badge(str(_("Superuser") if row.is_superuser else _("User")), tone="warning" if row.is_superuser else "secondary"), bool(row.is_superuser)

    def get_column_last_login_at_data(self, row: OldmanUser, **kwargs: object):
        """渲染最近登录时间。"""
        value = getattr(row, "last_login_at", None)
        if not value:
            return Markup(f'<span class="text-default-500">{escape(_("Never"))}</span>'), ""
        return value.strftime("%Y-%m-%d %H:%M"), value.isoformat()

    def get_column_action_data(self, row: OldmanUser, **kwargs: object):
        """渲染用户行级操作 om-dropdown。"""
        user_id = row.id
        status_label = _("Disable") if getattr(row, "is_active", False) else _("Enable")
        return (
            Markup(
                '<div class="om-dropdown">'
                '<button class="om-button om-button-soft-secondary om-button-sm" type="button" data-om-dropdown-toggle aria-expanded="false">'
                '<i class="ri-more-fill align-middle"></i>'
                "</button>"
                '<ul class="om-dropdown-menu om-dropdown-menu-end">'
                f'<li><a class="om-dropdown-item" href="/users/{user_id}/edit"><i class="ri-pencil-fill align-bottom mr-2 text-default-500"></i>{escape(_("Edit"))}</a></li>'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#user-password-modal" data-om-modal-url="/users/{user_id}/password-modal"><i class="ri-lock-password-line align-bottom mr-2 text-default-500"></i>{escape(_("Change Password"))}</button></li>'
                f'<li><button class="om-dropdown-item" type="button" data-om-modal-target="#user-status-modal" data-om-modal-url="/users/{user_id}/status-modal"><i class="ri-toggle-line align-bottom mr-2 text-default-500"></i>{escape(status_label)}</button></li>'
                f'<li><button class="om-dropdown-item text-red-700" type="button" data-om-modal-target="#user-delete-modal" data-om-modal-url="/users/{user_id}/delete-modal"><i class="ri-delete-bin-line align-bottom mr-2"></i>{escape(_("Delete"))}</button></li>'
                "</ul>"
                "</div>"
            ),
            "",
        )
