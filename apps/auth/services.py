"""后台认证服务。"""

from __future__ import annotations

import datetime as dt

from config.settings import settings
from sqlalchemy import select

from apps.auth.models import OldmanUser
from apps.auth.session import DashboardSessionData
from oldman.apps.admin.apps import app as admin_app
from oldman.db import db_manager
from oldman.web.auth import session_data_for_user


class UserManagementError(ValueError):
    """后台用户管理服务错误。"""


async def get_user_by_username(username: str) -> OldmanUser | None:
    """根据用户名读取后台用户。"""
    normalized_username = username.strip()
    if not normalized_username:
        return None

    async with db_manager.get_read_session() as session:
        result = await session.execute(select(OldmanUser).where(OldmanUser.username == normalized_username))
        return result.scalar_one_or_none()


async def authenticate_user(username: str, password: str) -> OldmanUser | None:
    """校验用户名密码并返回可登录的后台用户。"""
    user = await get_user_by_username(username)
    if not user or not user.is_active:
        return None
    if not user.is_staff:
        return None
    if admin_app.settings.require_superuser and not user.is_superuser:
        return None
    if not user.check_password(password):
        return None
    return user


async def touch_last_login(user_id: int) -> None:
    """更新后台用户最后登录时间。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return
        user.last_login_at = dt.datetime.utcnow()


async def get_user_by_id(user_id: int) -> OldmanUser | None:
    """根据 ID 读取后台用户。"""
    async with db_manager.get_read_session() as session:
        return await session.get(OldmanUser, user_id)


def change_user_password(user: OldmanUser, raw_password: str) -> None:
    """修改用户密码并写入新的 hash。"""
    user.set_password(raw_password)


def set_user_active(user: OldmanUser, is_active: bool, *, current_user_id: int | None) -> None:
    """修改用户启停状态，并拒绝禁用当前登录用户。"""
    if not is_active and current_user_id is not None and user.id == current_user_id:
        raise UserManagementError("cannot disable current user")
    user.is_active = bool(is_active)


def validate_user_delete(user: OldmanUser, *, current_user_id: int | None) -> None:
    """校验用户是否允许删除。"""
    if current_user_id is not None and user.id == current_user_id:
        raise UserManagementError("cannot delete current user")
    if user.is_superuser:
        raise UserManagementError("cannot delete superuser")


async def ensure_default_admin(username: str, password: str, email: str | None = None) -> OldmanUser:
    """确保默认管理员存在，供初始化脚本或本地开发使用。"""
    async with db_manager.get_session() as session:
        result = await session.execute(select(OldmanUser).where(OldmanUser.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = OldmanUser(username=username, email=email, display_name=username, is_active=True, is_staff=True, is_superuser=True, password_hash="")
            user.set_password(password)
            session.add(user)
            return user

        user.email = email or user.email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        if password:
            user.set_password(password)
        return user


def session_data(user: OldmanUser, request_ip: str) -> DashboardSessionData:
    """构建写入 Redis 的完整强类型后台会话。"""
    return session_data_for_user(
        DashboardSessionData,
        user,
        expiry=settings.web.session.expiry,
        login_ip=request_ip,
    )
