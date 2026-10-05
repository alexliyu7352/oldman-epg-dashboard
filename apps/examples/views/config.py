"""Dynamic configuration examples: a typed YAML file, and typed Redis values beside a native Redis set."""

from __future__ import annotations

from oldman.i18n import LazyTranslation
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound, router
from oldman.web.api import ApiErrorCode, ReplaceHtmlAction, form_error_response, form_response
from oldman.web.auth import form_value, login_required
from oldman.web.request import Request
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_fragment, render_template

from apps.examples import dynamic_config

from . import EXAMPLE_SECTIONS

FILE_OPERATIONS = {"read", "set_channel", "announce", "edit_outside", "break_outside", "restore"}
REDIS_OPERATIONS = {
    "read",
    "update_flags",
    "write_elsewhere",
    "wrong_type",
    "add_device",
    "remove_device",
    "check_device",
    "random_device",
    "restore",
}
MAX_OFFSET_MINUTES = 720
MAX_TEXT_LENGTH = 200
MAX_STREAMS = 20


def _page_context(page: str) -> dict[str, object]:
    section = EXAMPLE_SECTIONS["config"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_config_{page}",
        "active_section": "examples_config",
        "example_category": "config",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


def _field(request: Request, name: str) -> str:
    return form_value(request, name).strip()


def _invalid(field: str, message: LazyTranslation):
    return form_error_response(message, errors={field: message}, error_code=ApiErrorCode.INVALID_REQUEST)


@router.get("/examples/config/file", name="example_config_file_page")
@add_csrf_token()
@login_required()
async def example_config_file_page(request: Request):
    """Show the file as the store serves it; the first read writes the defaults if the file is missing."""
    context = _page_context("file")
    context.update(state=await dynamic_config.file_state(), check_interval=dynamic_config.CHECK_INTERVAL)
    return await render_template("pages/examples/config/file.html", context=context)


@router.post("/examples/config/file", name="example_config_file_operation")
@csrf_protect()
@login_required()
async def example_config_file_operation(request: Request):
    """Run the operation of the pressed button, then show the value, the hook count and the file."""
    operation = _field(request, "operation")
    if operation not in FILE_OPERATIONS:
        raise NotFound("Dynamic configuration example operation was not found")

    if operation == "set_channel":
        channel_id = _field(request, "channel_id")
        if not channel_id or len(channel_id) > 40:
            return _invalid("channel_id", _("Enter a channel ID of up to 40 characters."))
        raw = _field(request, "epg_offset_minutes")
        minutes = int(raw) if raw.lstrip("-").isdecimal() and raw.isascii() else None
        if minutes is None or not -MAX_OFFSET_MINUTES <= minutes <= MAX_OFFSET_MINUTES:
            return _invalid("epg_offset_minutes", _("Enter whole minutes between -720 and 720."))
        await dynamic_config.set_channel_offset(channel_id, minutes)
    elif operation in {"announce", "edit_outside"}:
        text = _field(request, "announcement")
        if len(text) > MAX_TEXT_LENGTH:
            return _invalid("announcement", _("Keep the announcement within 200 characters."))
        if operation == "announce":
            await dynamic_config.set_announcement(text)
        else:
            await dynamic_config.edit_announcement_outside(text)
    elif operation == "break_outside":
        await dynamic_config.break_file_outside()
    elif operation == "restore":
        await dynamic_config.restore_file()

    html = await render_fragment(
        request,
        "pages/examples/config/_file_result.html",
        operation=operation,
        state=await dynamic_config.file_state(),
        check_interval=dynamic_config.CHECK_INTERVAL,
    )
    return form_response(error_code=ApiErrorCode.OK, actions=[ReplaceHtmlAction(html=html, target="#config-file-result")])


@router.get("/examples/config/redis", name="example_config_redis_page")
@add_csrf_token()
@login_required()
async def example_config_redis_page(request: Request):
    """Show the Redis value and the device set; reading a missing key gives the defaults and writes nothing."""
    context = _page_context("redis")
    context.update(state=await dynamic_config.redis_state())
    return await render_template("pages/examples/config/redis.html", context=context)


@router.post("/examples/config/redis", name="example_config_redis_operation")
@csrf_protect()
@login_required()
async def example_config_redis_operation(request: Request):
    """Run the operation of the pressed button, then show both keys again."""
    operation = _field(request, "operation")
    if operation not in REDIS_OPERATIONS:
        raise NotFound("Dynamic configuration example operation was not found")

    context: dict[str, object] = {"operation": operation}
    if operation in {"update_flags", "write_elsewhere"}:
        banner = _field(request, "banner")
        if len(banner) > MAX_TEXT_LENGTH:
            return _invalid("banner", _("Keep the banner within 200 characters."))
        if operation == "write_elsewhere":
            await dynamic_config.write_banner_from_another_instance(banner)
        else:
            raw = _field(request, "max_streams_per_user")
            streams = int(raw) if raw.isascii() and raw.isdecimal() else None
            if streams is None or not 1 <= streams <= MAX_STREAMS:
                return _invalid("max_streams_per_user", _("Enter a whole number from 1 to 20."))
            await dynamic_config.update_flags(maintenance=_field(request, "maintenance") == "on", banner=banner, max_streams_per_user=streams)
    elif operation == "wrong_type":
        context["refusal"] = await dynamic_config.write_wrong_type()
    elif operation.endswith("_device"):
        devices = dynamic_config.allowed_devices()
        if operation == "random_device":
            context["device"] = await devices.random()
        else:
            device = _field(request, "device_id")
            if not device or len(device) > 40:
                return _invalid("device_id", _("Enter a device ID of up to 40 characters."))
            context["device"] = device
            action = {"add_device": devices.add, "remove_device": devices.remove, "check_device": devices.contains}[operation]
            context["answer"] = await action(device)
    elif operation == "restore":
        await dynamic_config.restore_redis()

    html = await render_fragment(request, "pages/examples/config/_redis_result.html", state=await dynamic_config.redis_state(), **context)
    return form_response(error_code=ApiErrorCode.OK, actions=[ReplaceHtmlAction(html=html, target="#config-redis-result")])
