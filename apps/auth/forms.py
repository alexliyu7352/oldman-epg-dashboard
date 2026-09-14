"""后台用户管理表单。"""

from __future__ import annotations

from sqlalchemy import select
from wtforms import BooleanField, DateTimeLocalField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from apps.auth.models import OldmanUser
from oldman.i18n import gettext_lazy as _
from oldman.web.auth.forms import (
    PASSWORD_MESSAGE,
    PASSWORD_PATTERN,
)
from oldman.web.components.forms import (
    DateTimePickerWidget,
    FieldLayout,
    TailwindForm,
    TailwindModelForm,
    TailwindTableFilterForm,
)

BOOLEAN_FILTER_CHOICES = [
    ("", _("All")),
    ("true", _("Yes")),
    ("false", _("No")),
]


class LoginForm(TailwindForm):
    """收集后台登录凭据。"""

    username = StringField(
        _("Username"),
        id="username",
        validators=[DataRequired()],
        render_kw={
            "required": True,
            "autocomplete": "username",
            "placeholder": _("Enter username"),
            "autofocus": True,
        },
    )
    password = PasswordField(
        _("Password"),
        id="password-input",
        validators=[DataRequired()],
        render_kw={
            "required": True,
            "autocomplete": "current-password",
            "placeholder": _("Enter password"),
        },
    )


class UserFilterForm(TailwindTableFilterForm):
    """后台用户列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search username, email, display name")})
    is_active = SelectField(_("Active"), choices=BOOLEAN_FILTER_CHOICES, validators=[Optional()])
    is_staff = SelectField(_("Staff"), choices=BOOLEAN_FILTER_CHOICES, validators=[Optional()])
    is_superuser = SelectField(_("Superuser"), choices=BOOLEAN_FILTER_CHOICES, validators=[Optional()])
    last_login_from = DateTimeLocalField(
        _("Last Login From"),
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )
    last_login_to = DateTimeLocalField(
        _("Last Login To"),
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )

    field_layout = (
        FieldLayout("q", "lg:col-span-4 md:col-span-6"),
        FieldLayout("is_active", "lg:col-span-2 md:col-span-6"),
        FieldLayout("is_staff", "lg:col-span-2 md:col-span-6"),
        FieldLayout("is_superuser", "lg:col-span-2 md:col-span-6"),
        FieldLayout("last_login_from", "lg:col-span-2 md:col-span-6"),
        FieldLayout("last_login_to", "lg:col-span-2 md:col-span-6"),
    )


class UserEditForm(TailwindModelForm):
    """后台用户基础资料编辑表单，不包含密码 hash 字段。"""

    current_user_id: int | None = None

    username = StringField(_("Username"), validators=[DataRequired(), Length(max=150)], render_kw={"required": True, "maxlength": 150})
    email = StringField(_("Email"), validators=[Optional(), Length(max=254)], render_kw={"maxlength": 254})
    display_name = StringField(_("Display Name"), validators=[Optional(), Length(max=150)], render_kw={"maxlength": 150})
    is_active = BooleanField(_("Active"))
    is_staff = BooleanField(_("Staff"))
    is_superuser = BooleanField(_("Superuser"))

    field_layout = (
        FieldLayout("username", "md:col-span-6"),
        FieldLayout("email", "md:col-span-6"),
        FieldLayout("display_name", "md:col-span-6"),
        FieldLayout("is_active", "md:col-span-4"),
        FieldLayout("is_staff", "md:col-span-4"),
        FieldLayout("is_superuser", "md:col-span-4"),
    )

    class Meta:
        """声明后台用户基础资料允许编辑的字段。"""

        model = OldmanUser
        fields = ["username", "email", "display_name", "is_active", "is_staff", "is_superuser"]

    async def clean_username(self) -> str:
        """校验用户名唯一性，避免数据库唯一索引异常变成 500。"""
        username = str(self.username.data or "").strip()
        if self.session is None or not username:
            return username

        result = await self.session.execute(select(OldmanUser).where(OldmanUser.username == username))
        existing = result.scalar_one_or_none()
        if existing is not None and int(existing.id) != self.instance_id():
            self.add_error("username", _("Username already exists"))
        return username

    async def clean_email(self) -> str | None:
        """校验邮箱唯一性，空邮箱仍按可选字段处理。"""
        email = str(self.email.data or "").strip()
        if not email:
            return None
        if self.session is None:
            return email

        result = await self.session.execute(select(OldmanUser).where(OldmanUser.email == email))
        existing = result.scalar_one_or_none()
        if existing is not None and int(existing.id) != self.instance_id():
            self.add_error("email", _("Email already exists"))
        return email

    async def clean(self) -> None:
        """拒绝当前登录用户通过编辑页禁用自己或移除自己的超级用户权限。"""
        await super().clean()
        current_user_id = getattr(self, "current_user_id", None)
        if current_user_id is None or self.instance_id() != int(current_user_id):
            return None
        if self.cleaned_data.get("is_active") is False:
            self.add_error("is_active", _("cannot disable current user"))
        if self.cleaned_data.get("is_staff") is False:
            self.add_error("is_staff", _("cannot remove current user staff access"))
        if self.cleaned_data.get("is_superuser") is False:
            self.add_error("is_superuser", _("cannot remove current user superuser access"))
        return None

    def instance_id(self) -> int:
        """返回当前编辑对象 ID，新建表单统一返回 0。"""
        try:
            return int(getattr(self.instance, "id", 0) or 0)
        except (TypeError, ValueError):
            return 0


class UserCreateForm(UserEditForm):
    """后台用户创建表单，负责把首次密码写入 hash。"""

    password = PasswordField(
        _("Password"),
        validators=[DataRequired(), Length(min=8, max=128), Regexp(PASSWORD_PATTERN, message=PASSWORD_MESSAGE)],
        render_kw={"required": True, "minlength": 8, "maxlength": 128, "pattern": PASSWORD_PATTERN, "autocomplete": "new-password"},
    )
    confirm_password = PasswordField(
        _("Confirm Password"),
        validators=[DataRequired(), Length(min=8, max=128)],
        render_kw={"required": True, "minlength": 8, "maxlength": 128, "autocomplete": "new-password"},
    )

    field_layout = (
        FieldLayout("username", "md:col-span-6"),
        FieldLayout("email", "md:col-span-6"),
        FieldLayout("display_name", "md:col-span-6"),
        FieldLayout("password", "md:col-span-6"),
        FieldLayout("confirm_password", "md:col-span-6"),
        FieldLayout("is_active", "md:col-span-4"),
        FieldLayout("is_staff", "md:col-span-4"),
        FieldLayout("is_superuser", "md:col-span-4"),
    )

    class Meta:
        """声明创建用户时基础资料字段仍不包含 password_hash。"""

        model = OldmanUser
        fields = ["username", "email", "display_name", "is_active", "is_staff", "is_superuser"]

    async def clean(self) -> None:
        """校验两次密码输入一致。"""
        await super().clean()
        if self.password.data != self.confirm_password.data:
            self.add_error("confirm_password", _("Passwords do not match"))

    async def save_user(self, *, commit: bool = False, session=None) -> OldmanUser:
        """保存新用户并通过 set_password 写入密码 hash。"""
        user = await self.save(commit=False, session=session)
        user.set_password(str(self.password.data or ""))
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save_user(commit=True) requires a session")
            active_session.add(user)
            flush_result = active_session.flush()
            import inspect

            if inspect.isawaitable(flush_result):
                await flush_result
        return user
