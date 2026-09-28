"""后台用户相关表单：登录、筛选、创建/编辑都来自框架，这里只把项目的 User 模型绑定进去。"""

from __future__ import annotations

from oldman.web.auth.forms import LoginForm, UserFilterForm, user_create_form_class, user_edit_form_class

from apps.auth.models import OldmanUser

UserEditForm = user_edit_form_class(OldmanUser)
UserCreateForm = user_create_form_class(OldmanUser)

__all__ = ["LoginForm", "UserCreateForm", "UserEditForm", "UserFilterForm"]
