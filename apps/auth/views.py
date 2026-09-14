"""后台登录与登出视图。"""

from __future__ import annotations

from urllib.parse import urlencode, urlparse

from config.settings import settings
from markupsafe import escape
from services.web import dashboard_language_registry

from apps.auth.decorators import admin_required
from apps.auth.forms import LoginForm, UserCreateForm, UserEditForm, UserFilterForm
from apps.auth.models import OldmanUser
from apps.auth.services import (
    UserManagementError,
    authenticate_user,
    change_user_password,
    get_user_by_id,
    session_data,
    set_user_active,
    touch_last_login,
    validate_user_delete,
)
from apps.auth.session import dashboard_session
from apps.auth.tables import UserTable
from oldman.auth.apps import app as auth_app
from oldman.db import db_manager
from oldman.i18n import gettext as _
from oldman.web.api import (
    ApiErrorCode,
    CloseModalAction,
    DefaultApiFormResponse,
    FeedbackAction,
    RedirectAction,
    ReloadTableAction,
    ResponseAction,
)
from oldman.web.auth import (
    UserPasswordForm,
    render_session_password_modal,
    save_language_preference,
    session_profile,
    update_session_password,
)
from oldman.web.messages import DashboardActivityAction
from oldman.web.messages.notifications import render_center_content
from oldman.web.request import Request
from oldman.web.response import json_response, redirect_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.session import Session
from oldman.web.sse import SSEStream, sse
from oldman.web.template import render_template

app = get_app()
app.add_route(UserTable.as_view(), UserTable.route_path, name=UserTable.route_name)


def form_value(request: Request, key: str, default: str = "") -> str:
    """从请求表单中读取单个字符串值。"""
    value = (request.form or {}).get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    return str(value)


def safe_next_url(raw_next_url: str | None) -> str:
    """返回安全的站内跳转地址，避免登录后开放重定向。"""
    if not raw_next_url:
        return "/"
    parsed = urlparse(raw_next_url)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not raw_next_url.startswith("/"):
        return "/"
    return raw_next_url


def login_error_message(error_code: str | None) -> str:
    """把 URL 中的错误代码转换为允许展示的登录错误文案。"""
    messages = {
        "invalid_credentials": _("Invalid username or password."),
    }
    return messages.get(error_code or "", "")


def login_error_url(next_url: str, error_code: str) -> str:
    """生成登录失败后的 GET 回跳地址，由登录页重新发放 CSRF token。"""
    query = urlencode({"next": next_url, "error": error_code})
    return f"/login?{query}"


def current_user_id(request: Request) -> int | None:
    """从当前会话读取登录用户 ID。"""
    return dashboard_session(request).user_id


if settings.web.sse.enabled:

    @app.get("/user-events", name="user_events")
    @admin_required()
    @sse.streaming(session_guard=True, login_url="/login")
    async def user_events(request: Request, stream: SSEStream) -> None:
        """Deliver shared low-frequency user events to one authenticated browser."""
        user_id = current_user_id(request)
        if user_id is None:
            raise RuntimeError("Authenticated Dashboard Session has no user id")
        await stream.subscribe_user(user_id)


def api_form_payload(message: str, *, status: int = 200, actions: list[ResponseAction] | None = None):
    """返回标准表单 JSON 响应。"""
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=message,
        actions=actions or [],
    )
    return json_response(payload.to_dict(), status=status)


def api_form_error(message: str, *, status: int = 200, errors: dict[str, str] | None = None):
    """返回标准表单错误 JSON 响应。"""
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.FORM_INVALID,
        message=message,
        errors=errors or {},
    )
    return json_response(payload.to_dict(), status=status)


def user_notification(
    title: str,
    description: str,
    *,
    tone: str = "primary",
    icon: str = "ri-user-settings-line",
) -> DashboardActivityAction:
    """生成用户管理动作的顶栏临时通知。"""
    return DashboardActivityAction(
        title=title,
        description=description,
        tone=tone,
        icon=icon,
        href="/users",
        time=_("Just now"),
    )


@app.post("/preferences/language", name="preferences_language")
@app.post("/user-session/language", name="user_session_language")
@csrf_protect()
async def language_preference(request: Request):
    """保存 dashboard 语言偏好，供前端无刷新语言切换后同步后端。"""
    return save_language_preference(
        request,
        registry=dashboard_language_registry(),
    )


@app.get("/login", name="login")
@add_csrf_token()
async def login_page(request: Request):
    """渲染后台登录页。"""
    session = getattr(request.ctx, "session", None)
    next_url = safe_next_url(request.args.get("next"))
    if session and session.is_authenticated():
        return redirect_response(next_url)

    return await render_template(
        "pages/login.html",
        context={
            "login_form": LoginForm(request=request, csrf_token=request.ctx.csrf_token),
            "next_url": next_url,
            "csrf_token": request.ctx.csrf_token,
            "login_error": login_error_message(request.args.get("error")),
        },
    )


@app.get("/user-session", name="user_session")
@add_csrf_token()
@admin_required()
async def user_session(request: Request):
    """渲染当前后台会话页，只展示当前用户和登录态，不承担用户 CRUD。"""
    return await render_template(
        "pages/user_session/index.html",
        context={
            "active_section": "system",
            "active_page": "user_session",
            "session_profile": session_profile(dashboard_session(request)),
            "session_path": "/user-session",
            "password_modal_path": "/user-session/password-modal",
            "logout_path": "/logout",
        },
    )


@app.get("/user-notifications", name="user_notifications")
@admin_required()
async def user_notifications(request: Request):
    """Render the host shell around the framework's shared notification center."""
    user_id = current_user_id(request)
    if user_id is None:
        raise RuntimeError("Authenticated Dashboard Session has no user id")
    return await render_template(
        "pages/user_notifications.html",
        context={
            "active_section": "system",
            "active_page": "user_notifications",
            "notification_center_content": await render_center_content(
                request,
                user_id=user_id,
            ),
        },
    )


@app.get("/users", name="users")
@admin_required()
async def users_index(request: Request):
    """渲染后台用户管理列表。"""
    filter_names = ("is_active", "is_staff", "is_superuser", "last_login_from", "last_login_to")
    table = UserTable(
        request=request,
        initial_filters={name: request.args.get(name, "").strip() for name in filter_names},
        initial_query=request.args.get("q", "").strip(),
    )
    return await render_template(
        "pages/users/index.html",
        context={
            "active_section": "system",
            "active_page": "users",
            "filter_form": UserFilterForm.from_query(request),
            "table": table,
        },
    )


@app.get("/users/new", name="users_new")
@add_csrf_token()
@admin_required()
async def users_new(request: Request):
    """渲染后台用户创建页。"""
    form = UserCreateForm(request=request, csrf_token=request.ctx.csrf_token)
    return await render_template("pages/users/form.html", context={"active_section": "system", "active_page": "users", "form": form, "user": None})


@app.post("/users/new", name="users_create")
@csrf_protect()
@admin_required()
async def users_create(request: Request):
    """处理后台用户创建提交。"""
    async with db_manager.get_session() as session:
        form = UserCreateForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        user = await form.save_user(commit=True, session=session)
        username = str(user.username)
    return api_form_payload(
        _("User created"),
        actions=[
            FeedbackAction(
                target="#users-form-feedback",
                title=_("User created"),
                text=_("%(username)s can now access the dashboard.", username=username),
                icon="success",
            ),
            user_notification(
                _("User created"),
                _("%(username)s can now access the dashboard.", username=username),
                tone="success",
                icon="ri-user-add-line",
            ),
            RedirectAction(url=f"/users/{user.id}/edit", delay_ms=1200),
        ],
    )


@app.get("/users/<user_id:int>/edit", name="users_edit_page")
@add_csrf_token()
@admin_required()
async def users_edit_page(request: Request, user_id: int):
    """渲染后台用户编辑页。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return redirect_response("/users")
    form = UserEditForm(request=request, instance=user, csrf_token=request.ctx.csrf_token)
    return await render_template("pages/users/form.html", context={"active_section": "system", "active_page": "users", "form": form, "user": user})


@app.post("/users/<user_id:int>/edit", name="users_update")
@csrf_protect()
@admin_required()
async def users_update(request: Request, user_id: int):
    """处理后台用户基础资料保存。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return api_form_error(_("User not found"), status=404)
        form = UserEditForm.from_request(request, instance=user, session=session)
        form.current_user_id = current_user_id(request)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        await form.save(commit=True, session=session)
        username = str(user.username)
    return api_form_payload(
        _("User saved"),
        actions=[
            FeedbackAction(
                target="#users-form-feedback",
                title=_("User saved"),
                text=_("%(username)s profile was updated.", username=username),
                icon="success",
            ),
            user_notification(
                _("User saved"),
                _("%(username)s profile was updated.", username=username),
                tone="primary",
                icon="ri-settings-3-line",
            ),
            RedirectAction(url=f"/users/{user_id}/edit", delay_ms=1200),
        ],
    )


@app.get("/users/<user_id:int>/password-modal", name="users_password_modal")
@add_csrf_token()
@admin_required()
async def users_password_modal(request: Request, user_id: int):
    """返回修改密码弹窗表单片段。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return json_response(
            {"title": _("Change Password"), "html": f'<p class="text-default-500 mb-0">{escape(_("User not found."))}</p>'},
            status=404,
        )
    form = UserPasswordForm(request=request, csrf_token=request.ctx.csrf_token)
    template = request.app.ext.environment.get_template(
        "oldman/auth/partials/password_form.html"
    )
    html = await template.render_async(
        action=f"/users/{user_id}/password",
        user=user,
        form=form,
    )
    return json_response({"title": f'{_("Change Password")} · {escape(user.username)}', "html": html})


@app.post("/users/<user_id:int>/password", name="users_password_update")
@csrf_protect()
@admin_required()
async def users_password_update(request: Request, user_id: int):
    """处理修改后台用户密码。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return api_form_error(_("User not found"), status=404)
        form = UserPasswordForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        username = str(user.username)
        change_user_password(user, str(form.cleaned_data["password"]))
        session.add(user)
    return api_form_payload(
        _("Password changed"),
        actions=[
            FeedbackAction(target="#users-feedback", title=_("Password changed"), text=_("%(username)s password was updated.", username=username), icon="success"),
            user_notification(
                _("Password changed"),
                _("%(username)s password was updated.", username=username),
                tone="success",
                icon="ri-lock-password-line",
            ),
            CloseModalAction(),
            ReloadTableAction(target="#users-table"),
        ],
    )


@app.get("/user-session/password-modal", name="user_session_password_modal")
@add_csrf_token()
@admin_required()
async def user_session_password_modal(request: Request):
    """返回当前登录用户的修改密码弹窗片段，不允许通过 URL 指定其他用户。"""
    return await render_session_password_modal(
        request,
        action="/user-session/password",
        auth_settings=auth_app.settings,
        db_manager=db_manager,
    )


@app.post("/user-session/password", name="user_session_password_update")
@csrf_protect()
@admin_required()
async def user_session_password_update(request: Request):
    """处理当前登录用户的密码修改，避免会话页越权修改任意用户。"""
    session = dashboard_session(request)
    message = _("Session password changed", request=request)
    description = _(
        "%(username)s password was updated.",
        request=request,
        username=session.username,
    )
    return await update_session_password(
        request,
        success_actions=(
            DashboardActivityAction(
                title=message,
                description=description,
                tone="success",
                icon="ri-lock-password-line",
                href="/user-session",
                time=_("Just now", request=request),
            ),
        ),
        auth_settings=auth_app.settings,
        db_manager=db_manager,
    )


@app.get("/users/<user_id:int>/status-modal", name="users_status_modal")
@add_csrf_token()
@admin_required()
async def users_status_modal(request: Request, user_id: int):
    """返回启停用户确认弹窗。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return json_response(
            {"title": _("Change Status"), "html": f'<p class="text-default-500 mb-0">{escape(_("User not found."))}</p>'},
            status=404,
        )
    template = request.app.ext.environment.get_template("partials/users/status_form.html")
    html = await template.render_async(user=user, csrf_token=request.ctx.csrf_token, target_active=not user.is_active)
    action_label = _("Enable") if not user.is_active else _("Disable")
    return json_response({"title": f'{action_label} {_("User")} · {escape(user.username)}', "html": html})


@app.post("/users/<user_id:int>/status", name="users_status_update")
@csrf_protect()
@admin_required()
async def users_status_update(request: Request, user_id: int):
    """处理后台用户启停提交。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return api_form_error(_("User not found"), status=404)
        target_active = form_value(request, "is_active").strip().lower() in {"1", "true", "yes", "on"}
        try:
            set_user_active(user, target_active, current_user_id=current_user_id(request))
        except UserManagementError as exc:
            return api_form_error(str(exc))
        username = str(user.username)
        session.add(user)
    return api_form_payload(
        _("User status updated"),
        actions=[
            FeedbackAction(
                target="#users-feedback",
                title=_("User status updated"),
                text=_("%(username)s is now active.", username=username) if target_active else _("%(username)s is now disabled.", username=username),
                icon="warning",
            ),
            user_notification(
                _("User status updated"),
                _("%(username)s is now active.", username=username) if target_active else _("%(username)s is now disabled.", username=username),
                tone="warning",
                icon="ri-toggle-line",
            ),
            CloseModalAction(),
            ReloadTableAction(target="#users-table"),
        ],
    )


@app.get("/users/<user_id:int>/delete-modal", name="users_delete_modal")
@add_csrf_token()
@admin_required()
async def users_delete_modal(request: Request, user_id: int):
    """返回删除用户确认弹窗。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return json_response(
            {"title": _("Delete User"), "html": f'<p class="text-default-500 mb-0">{escape(_("User not found."))}</p>'},
            status=404,
        )
    template = request.app.ext.environment.get_template("partials/users/delete_form.html")
    html = await template.render_async(user=user, csrf_token=request.ctx.csrf_token)
    return json_response({"title": f'{_("Delete User")} · {escape(user.username)}', "html": html})


@app.post("/users/<user_id:int>/delete", name="users_delete")
@csrf_protect()
@admin_required()
async def users_delete(request: Request, user_id: int):
    """处理后台用户删除提交。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return api_form_error(_("User not found"), status=404)
        try:
            validate_user_delete(user, current_user_id=current_user_id(request))
        except UserManagementError as exc:
            return api_form_error(str(exc))
        username = str(user.username)
        await session.delete(user)
    return api_form_payload(
        _("User deleted"),
        actions=[
            FeedbackAction(
                target="#users-feedback",
                title=_("User deleted"),
                text=_("%(username)s was removed from dashboard access.", username=username),
                icon="success",
            ),
            user_notification(
                _("User deleted"),
                _("%(username)s was removed from dashboard access.", username=username),
                tone="danger",
                icon="ri-delete-bin-line",
            ),
            CloseModalAction(),
            ReloadTableAction(target="#users-table"),
        ],
    )


@app.post("/login", name="login_submit")
@csrf_protect()
async def login_submit(request: Request):
    """处理后台用户名密码登录。"""
    next_url = safe_next_url(form_value(request, "next") or request.args.get("next"))
    username = form_value(request, "username").strip()
    password = form_value(request, "password")
    user = await authenticate_user(username, password)
    if user is None:
        return redirect_response(login_error_url(next_url, "invalid_credentials"), status=303)

    session_manager = Session.get_session_manager(request)
    authenticated_session = session_data(user, request.ip or request.client_ip or "")
    new_session_id = await session_manager.exclusive_login(authenticated_session)
    await touch_last_login(user.id)
    response = redirect_response(next_url)
    session_manager.update_session_id_to_cookie(response, new_session_id, authenticated_session)
    return response


@app.get("/logout", name="logout")
async def logout(request: Request):
    """退出后台登录。"""
    await Session.logout_session(request)
    return redirect_response("/login")
