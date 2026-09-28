"""后台登录与登出视图。"""

from __future__ import annotations

from markupsafe import escape
from oldman.auth import (
    UserManagementError,
    get_user_by_id,
    has_staff_access,
    is_ordinary_user,
    set_user_active,
    user_access_flags,
    user_identity,
    validate_user_delete,
)
from oldman.auth.apps import app as auth_app
from oldman.db import db_manager
from oldman.i18n import gettext as _
from oldman.web import router
from oldman.web.api import (
    CloseModalAction,
    FeedbackAction,
    RedirectAction,
    ReloadTableAction,
    form_error_response,
    form_response,
    modal_not_found_response,
    modal_response,
)
from oldman.web.auth import (
    INVALID_CREDENTIALS,
    RATE_LIMITED,
    SIGN_IN_AGAIN_DELAY_MS,
    LoginRateLimit,
    PasswordResetFlow,
    TokenFlow,
    UserPasswordForm,
    authenticated_session,
    form_value,
    login_error_message,
    login_error_url,
    login_user,
    logout_user,
    remember_me_requested,
    render_session_password_modal,
    revoke_user_logins,
    safe_next_url,
    save_language_preference,
    session_profile,
    staff_required,
    update_session_password,
    user_delete_modal_response,
    user_status_modal_response,
)
from oldman.web.i18n import language_registry
from oldman.web.messages import DashboardActivityAction
from oldman.web.messages.notifications import render_center_content
from oldman.web.request import Request
from oldman.web.response import json_response, redirect_response
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.sse import SSEStream, sse
from oldman.web.template import render_fragment, render_template

from apps.auth.forms import LoginForm, UserCreateForm, UserEditForm, UserFilterForm
from apps.auth.models import OldmanUser
from apps.auth.services import authenticate_user
from apps.auth.tables import UserTable
from config.settings import settings

router.add_route(UserTable.as_view(), UserTable.route_path, name=UserTable.route_name)


if settings.web.sse.enabled:

    @router.get("/user-events", name="user_events")
    @staff_required(user_keyword="user_id")
    @sse.streaming(session_guard=True, login_url="/login")
    async def user_events(request: Request, stream: SSEStream, *, user_id: int) -> None:
        """Deliver shared low-frequency user events to one authenticated browser."""
        await stream.subscribe_user(user_id)


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


@router.post("/preferences/language", name="preferences_language")
@router.post("/user-session/language", name="user_session_language")
@csrf_protect()
async def language_preference(request: Request):
    """保存 dashboard 语言偏好，供前端无刷新语言切换后同步后端。"""
    return save_language_preference(
        request,
        registry=language_registry(request),
    )


# 失败的登录按 IP 和用户名各记一次，限额在 auth.login 设置里。
sign_in_limit = LoginRateLimit()


async def render_login_page(request: Request, *, error: object, next_url: str):
    """登录页本身：GET 和被限流的 POST 都渲染它。"""
    return await render_template(
        "pages/login.html",
        context={
            "login_form": LoginForm(request=request, csrf_token=request.ctx.csrf_token),
            "next_url": next_url,
            "csrf_token": request.ctx.csrf_token,
            "login_error": login_error_message(error),
        },
    )


@router.get("/login", name="login")
@add_csrf_token()
async def login_page(request: Request):
    """渲染后台登录页。"""
    session = getattr(request.ctx, "session", None)
    next_url = safe_next_url(request.args.get("next"))
    if session and session.is_authenticated():
        return redirect_response(next_url)

    return await render_login_page(request, error=request.args.get("error"), next_url=next_url)


@router.get("/user-session", name="user_session")
@add_csrf_token()
@staff_required()
async def user_session(request: Request):
    """渲染当前后台会话页，只展示当前用户和登录态，不承担用户 CRUD。"""
    return await render_template(
        "pages/user_session/index.html",
        context={
            "active_section": "system",
            "active_page": "user_session",
            "session_profile": session_profile(authenticated_session(request)),
            "session_path": "/user-session",
            "password_modal_path": "/user-session/password-modal",
            "logout_path": "/logout",
        },
    )


@router.get("/user-notifications", name="user_notifications")
@staff_required(user_keyword="user_id")
async def user_notifications(request: Request, *, user_id: int):
    """Render the host shell around the framework's shared notification center."""
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


@router.get("/users", name="users")
@staff_required()
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


@router.get("/users/new", name="users_new")
@add_csrf_token()
@staff_required()
async def users_new(request: Request):
    """渲染后台用户创建页。"""
    # 装了 roles App 时表单要从数据库列出角色，所以渲染放在读会话里。
    async with db_manager.get_read_session() as session:
        form = UserCreateForm(request=request, session=session, csrf_token=request.ctx.csrf_token)
        return await render_template("pages/users/form.html", context={"active_section": "system", "active_page": "users", "form": form, "user": None})


@router.post("/users/new", name="users_create")
@csrf_protect()
@staff_required()
async def users_create(request: Request):
    """处理后台用户创建提交。"""
    async with db_manager.get_session() as session:
        form = UserCreateForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        user = await form.save(commit=True, session=session)
        # 新用户这时才有 id，勾选的角色跟它一起写入。
        await form.save_roles(session, user)
        username = str(user.username)
    return form_response(
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


@router.get("/users/<user_id:int>/edit", name="users_edit_page")
@add_csrf_token()
@staff_required()
async def users_edit_page(request: Request, user_id: int):
    """渲染后台用户编辑页。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return redirect_response("/users")
    # 表单要读出角色列表和该用户持有的角色，渲染放在读会话里。
    async with db_manager.get_read_session() as session:
        form = UserEditForm(request=request, instance=user, session=session, csrf_token=request.ctx.csrf_token)
        return await render_template("pages/users/form.html", context={"active_section": "system", "active_page": "users", "form": form, "user": user})


@router.post("/users/<user_id:int>/edit", name="users_update")
@csrf_protect()
@staff_required(user_keyword="current_user_id")
async def users_update(request: Request, user_id: int, *, current_user_id: int):
    """处理后台用户基础资料保存。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return form_error_response(_("User not found"), status=404)
        form = UserEditForm.from_request(request, instance=user, session=session)
        form.current_user_id = current_user_id
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        access_before = user_access_flags(user)
        await form.save(commit=True, session=session)
        roles_changed = await form.save_roles(session, user)
        username = str(user.username)
        access_changed = user_access_flags(user) != access_before or roles_changed
    # 登录带着打开时的启用、staff、超级用户标志和角色，任何一项变了就得结束。表单不许去掉自己的权限；
    # 给自己加权限会结束自己的会话，下面的跳转随后落到登录页。
    if access_changed:
        await revoke_user_logins(request, user_id)
    return form_response(
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


@router.get("/users/<user_id:int>/password-modal", name="users_password_modal")
@add_csrf_token()
@staff_required()
async def users_password_modal(request: Request, user_id: int):
    """返回修改密码弹窗表单片段。"""
    user = await get_user_by_id(user_id)
    if user is None:
        return modal_not_found_response(_("Change Password"), _("User not found."))
    form = UserPasswordForm(request=request, csrf_token=request.ctx.csrf_token)
    html = await render_fragment(
        request,
        "oldman/auth/partials/password_form.html",
        action=f"/users/{user_id}/password",
        user=user,
        form=form,
    )
    return modal_response(f'{_("Change Password")} · {escape(user.username)}', html=html)


@router.post("/users/<user_id:int>/password", name="users_password_update")
@csrf_protect()
@staff_required()
async def users_password_update(request: Request, user_id: int):
    """处理修改后台用户密码。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return form_error_response(_("User not found"), status=404)
        # staff 和超级用户账号只归超级用户管理。
        if not request.ctx.user.is_superuser and not is_ordinary_user(user):
            return form_error_response(_("Permission denied"))
        form = UserPasswordForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        username = str(user.username)
        user.set_password(str(form.cleaned_data["password"]))
        session.add(user)
        target_user_id = user_identity(user)
    # 和其他两条改密路径同一条规则：旧口令开出来的会话跟着旧口令一起结束。
    # 管理员改的是自己那一行时，结束的就是自己的会话。
    if await revoke_user_logins(request, target_user_id):
        return form_response(
            _("Password changed"),
            actions=[
                FeedbackAction(
                    title=_("Password changed"),
                    text=_("Your password was updated. Please sign in again."),
                    icon="success",
                ),
                RedirectAction(url="/login", delay_ms=SIGN_IN_AGAIN_DELAY_MS),
            ],
        )
    return form_response(
        _("Password changed"),
        actions=[
            FeedbackAction(
                target="#users-feedback",
                title=_("Password changed"),
                text=_("%(username)s was signed out and needs the new password.", username=username),
                icon="success",
            ),
            user_notification(
                _("Password changed"),
                _("%(username)s was signed out and needs the new password.", username=username),
                tone="success",
                icon="ri-lock-password-line",
            ),
            CloseModalAction(),
            ReloadTableAction(target="#users-table"),
        ],
    )


@router.get("/user-session/password-modal", name="user_session_password_modal")
@add_csrf_token()
@staff_required()
async def user_session_password_modal(request: Request):
    """返回当前登录用户的修改密码弹窗片段，不允许通过 URL 指定其他用户。"""
    return await render_session_password_modal(
        request,
        action="/user-session/password",
        auth_settings=auth_app.settings,
        db_manager=db_manager,
    )


@router.post("/user-session/password", name="user_session_password_update")
@csrf_protect()
@staff_required()
async def user_session_password_update(request: Request):
    """处理当前登录用户的密码修改，避免会话页越权修改任意用户。

    改完密码所有会话都会失效（包括当前这个），所以不再往活动菜单里加条目——
    页面 1.5 秒后就跳登录页了，加了也没人看得到。
    """
    return await update_session_password(
        request,
        login_url="/login",
        auth_settings=auth_app.settings,
        db_manager=db_manager,
    )


@router.get("/users/<user_id:int>/status-modal", name="users_status_modal")
@add_csrf_token()
@staff_required()
async def users_status_modal(request: Request, user_id: int):
    """返回启停用户确认弹窗。"""
    user = await get_user_by_id(user_id)
    return await user_status_modal_response(request, user, action=f"/users/{user_id}/status")


@router.post("/users/<user_id:int>/status", name="users_status_update")
@csrf_protect()
@staff_required(user_keyword="current_user_id")
async def users_status_update(request: Request, user_id: int, *, current_user_id: int):
    """处理后台用户启停提交。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return form_error_response(_("User not found"), status=404)
        # staff 和超级用户账号只归超级用户管理。
        if not request.ctx.user.is_superuser and not is_ordinary_user(user):
            return form_error_response(_("Permission denied"))
        target_active = form_value(request, "is_active").strip().lower() in {"1", "true", "yes", "on"}
        try:
            set_user_active(user, target_active, current_user_id=current_user_id)
        except UserManagementError as exc:
            return form_error_response(str(exc))
        username = str(user.username)
        session.add(user)
    # 每次停用都结束登录，不只在状态翻转时：早先停用的用户可能还留着登录。
    if not target_active:
        await revoke_user_logins(request, user_id)
    return form_response(
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


@router.get("/users/<user_id:int>/delete-modal", name="users_delete_modal")
@add_csrf_token()
@staff_required()
async def users_delete_modal(request: Request, user_id: int):
    """返回删除用户确认弹窗。"""
    user = await get_user_by_id(user_id)
    return await user_delete_modal_response(request, user, action=f"/users/{user_id}/delete")


@router.post("/users/<user_id:int>/delete", name="users_delete")
@csrf_protect()
@staff_required(user_keyword="current_user_id")
async def users_delete(request: Request, user_id: int, *, current_user_id: int):
    """处理后台用户删除提交。"""
    async with db_manager.get_session() as session:
        user = await session.get(OldmanUser, user_id)
        if user is None:
            return form_error_response(_("User not found"), status=404)
        # staff 和超级用户账号只归超级用户管理。
        if not request.ctx.user.is_superuser and not is_ordinary_user(user):
            return form_error_response(_("Permission denied"))
        try:
            validate_user_delete(user, current_user_id=current_user_id)
        except UserManagementError as exc:
            return form_error_response(str(exc))
        username = str(user.username)
        await session.delete(user)
    await revoke_user_logins(request, user_id)
    return form_response(
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


@router.post("/login", name="login_submit")
@csrf_protect()
@add_csrf_token()
async def login_submit(request: Request):
    """处理后台用户名密码登录。"""
    next_url = safe_next_url(form_value(request, "next") or request.args.get("next"))
    username = form_value(request, "username").strip()
    # 先看窗口再验密码：额度用完的请求不该再花一次 PBKDF2。
    retry_after = await sign_in_limit.retry_after(request, username)
    if retry_after is not None:
        response = await render_login_page(request, error=RATE_LIMITED, next_url=next_url)
        response.status = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    user = await authenticate_user(request, username, form_value(request, "password"))
    if user is None:
        await sign_in_limit.record_failure(request, username)
        return redirect_response(login_error_url("/login", next_url, INVALID_CREDENTIALS), status=303)
    return await login_user(request, user, response=redirect_response(next_url), remember=remember_me_requested(request))


@router.get("/logout", name="logout")
async def logout(request: Request):
    """退出后台登录。"""
    return await logout_user(request, "/login")


# 找回密码：六个视图、限流、发信、token 校验、no-store 头都在框架的 PasswordResetFlow 里，这里只给路径和模板前缀。
password_reset_flow = PasswordResetFlow(
    request_path="/password-reset",
    sent_path="/password-reset/sent",
    done_path="/password-reset/done",
    login_path="/login",
    home_path="/",
    confirm_path=lambda uidb64, token: f"/password-reset/{uidb64}/{token}",
    site_name="Oldman",
)
password_reset_flow.register_routes(template_prefix="pages/password_reset")

# 访问令牌：取令牌、刷新、注销三个接口都在框架的 TokenFlow 里。和登录页一样只发给 staff；
# 签名密钥来自 web.auth.jwt.secret，没配置时这三个接口第一次被请求就报错。
token_flow = TokenFlow(accept_user=has_staff_access)
token_flow.register_routes(
    obtain_path="/api/token",
    refresh_path="/api/token/refresh",
    revoke_path="/api/token/revoke",
)
