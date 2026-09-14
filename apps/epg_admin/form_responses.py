"""EPG 后台表单响应协议辅助函数。"""

from __future__ import annotations

from typing import Any

from oldman.web.api import ApiErrorCode, DefaultApiFormResponse, RedirectAction
from oldman.web.components.forms import OldmanForm
from oldman.web.response import html_response, json_response, redirect_response
from oldman.web.template import render_template


def accepts_json_form_response(request: Any) -> bool:
    """判断当前表单提交是否要求 JSON 表单协议响应。"""
    accept = str((getattr(request, "headers", {}) or {}).get("accept", "")).lower()
    return "application/json" in accept


def accepts_html_form_response(request: Any) -> bool:
    """判断当前表单提交是否要求 HTML 局部片段响应。"""
    accept = str((getattr(request, "headers", {}) or {}).get("accept", "")).lower()
    return "text/html" in accept and not accepts_json_form_response(request)


async def form_error_response(
    request: Any,
    form: OldmanForm,
    *,
    template: str,
    context: dict[str, Any],
    cancel_url: str,
    status: int = 422,
):
    """按 Accept 返回表单错误响应，支持 JSON schema、HTML 片段和普通整页渲染。"""
    if accepts_json_form_response(request):
        return json_response(form.to_api_response().to_dict(), status=200)

    if accepts_html_form_response(request):
        return html_response(str(await form.render(cancel_url=cancel_url)), status=status)

    return await render_template(template, context=context, status=status)


def form_success_response(request: Any, redirect_url: str):
    """按 Accept 返回表单成功响应，JSON 模式返回 redirect 指令，普通模式返回 303。"""
    if accepts_json_form_response(request):
        payload = DefaultApiFormResponse(
            error_code=ApiErrorCode.OK,
            actions=[RedirectAction(url=redirect_url)],
        )
        return json_response(payload.to_dict(), status=200)

    return redirect_response(redirect_url, status=303)
