"""Executable persistent and temporary notification examples."""

from __future__ import annotations

from apps.auth.decorators import admin_required
from apps.auth.session import dashboard_session
from oldman.i18n import gettext_lazy as _
from oldman.web.api import ApiErrorCode, DefaultApiResponse
from oldman.web.messages import MessageFormat, MessageLevel
from oldman.web.messages.notifications import NotificationPresentation, notifications
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_NOTIFICATION_PAGES = frozenset({"generator", "center", "realtime"})
_TRUSTED_BODY = (
    '<strong data-example-notification-html="true">Trusted service status</strong>'
    "<br>Only fixed server markup uses HTML mode."
)

app = get_app()


@app.get("/examples/notifications/<page:str>", name="example_notifications_page")
@add_csrf_token()
@admin_required()
async def example_notifications_page(request: Request, page: str):
    """Render one notification example without adding another user event stream."""
    if page not in OWNED_NOTIFICATION_PAGES:
        return await _render_example(request, "notifications", page)
    context = _page_context(page)
    if page == "center":
        context["unread_count"] = await notifications.unread_count(_user_id(request))
    return await render_template(
        f"pages/examples/notifications/{page}.html",
        context=context,
    )


@app.post("/examples/notifications/send", name="example_notification_send")
@csrf_protect()
@admin_required()
async def example_notification_send(request: Request):
    """Create or push one notification for the current authenticated user only."""
    form = request.form
    if form is None:
        return _invalid_input()
    try:
        mode = _required(form.get("mode"))
        level = MessageLevel(_required(form.get("level")))
        presentation = NotificationPresentation(_required(form.get("presentation")))
        title = _required(form.get("title"))
        body = _optional(form.get("body"))
        href = _optional(form.get("href"))
        icon = _optional(form.get("icon"))
        content_mode = _required(form.get("content_mode"))
        if content_mode == "trusted_html":
            body = _TRUSTED_BODY
            message_format = MessageFormat.HTML
        elif content_mode == "text":
            message_format = MessageFormat.TEXT
        else:
            raise ValueError("invalid content mode")

        user_id = _user_id(request)
        if mode == "persistent":
            row = await notifications.create(
                user_id=user_id,
                title=title,
                body=body,
                level=level,
                format=message_format,
                presentation=presentation,
                href=href,
                icon=icon,
            )
            data: dict[str, object] = {"mode": mode, "notification_id": row.id}
        elif mode == "temporary":
            delivered = await notifications.push(
                user_id=user_id,
                title=title,
                body=body,
                level=level,
                format=message_format,
                presentation=presentation,
                href=href,
                icon=icon,
            )
            data = {"mode": mode, "delivered": delivered}
        else:
            raise ValueError("invalid notification mode")
    except (TypeError, ValueError):
        return _invalid_input()
    return api_response(DefaultApiResponse(data=data))


def _required(value: object) -> str:
    """Return one non-empty form string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required field")
    return value.strip()


def _optional(value: object) -> str | None:
    """Normalize one optional form string."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _user_id(request: Request) -> int:
    """Require the integer identity guaranteed by the staff guard."""
    user_id = dashboard_session(request).user_id
    if user_id is None:
        raise RuntimeError("Authenticated Dashboard Session has no user id")
    return user_id


def _invalid_input():
    """Return one structured business error for forged or invalid controls."""
    return api_response(
        DefaultApiResponse(
            error_code=ApiErrorCode.INVALID_REQUEST,
            message=_("Invalid notification input."),
        )
    )


def _page_context(page: str) -> dict[str, object]:
    """Build the shared examples shell context for one notification page."""
    section = EXAMPLE_SECTIONS["notifications"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_notifications_{page}",
        "active_section": "examples_notifications",
        "example_category": "notifications",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = [
    "OWNED_NOTIFICATION_PAGES",
    "example_notification_send",
    "example_notifications_page",
]
