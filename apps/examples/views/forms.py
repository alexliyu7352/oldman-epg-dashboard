"""Executable Form example pages and submissions."""

from __future__ import annotations

from typing import Any

from apps.auth.decorators import admin_required
from apps.examples import services
from apps.examples.forms import FORM_PAGE_FORMS, ContainerExampleForm, StreamProfileForm
from apps.examples.models import ExampleStreamProfile
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound
from oldman.web.api import (
    ApiErrorCode,
    CloseModalAction,
    DefaultApiFormResponse,
    FeedbackAction,
    ReplaceHtmlAction,
    ResponseAction,
)
from oldman.web.request import Request
from oldman.web.response import html_response, json_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_FORM_PAGES = frozenset((*FORM_PAGE_FORMS, "selects", "autocomplete", "upload", "json-list"))
DUAL_MODE_FORM_PAGES = frozenset(
    {"basics", "slug", "input-spinner", "tags", "color-picker", "rich-text", "multi-step"}
)

app = get_app()


@app.get("/examples/forms/<page:str>", name="example_forms_page")
@add_csrf_token()
@admin_required()
async def example_forms_page(request: Request, page: str):
    """Render one concrete Form example page."""
    if page not in OWNED_FORM_PAGES:
        return await _render_example(request, "forms", page)
    context = _page_context(page)

    if page in {"selects", "autocomplete"}:
        from .data_inputs import render_remote_form_page

        return await render_remote_form_page(request, page, context)
    if page == "upload":
        from .storage import render_upload_page

        return await render_upload_page(request, category="forms")
    if page == "json-list":
        profiles = await services.list_stream_profiles()
        selected = profiles[0] if profiles else None
        context.update(
            profiles=profiles,
            create_form=StreamProfileForm(request=request, prefix="create"),
            edit_form=StreamProfileForm(request=request, instance=selected, prefix="edit") if selected else None,
            selected_profile=selected,
        )
        template = "pages/examples/forms/json_list.html"
    elif page == "multi-step":
        async with db_manager.get_read_session() as session:
            form_class = FORM_PAGE_FORMS[page]
            context["form_cards"] = [
                _form_card(
                    form_class(request=request, session=session, prefix="html"),
                    page,
                    "html",
                    _("HTML response"),
                ),
                _form_card(
                    form_class(request=request, session=session, prefix="json"),
                    page,
                    "json",
                    _("JSON response actions"),
                ),
            ]
            context["show_static_fields"] = False
            context["show_slider"] = False
            context["show_color_picker"] = False
            context["show_rich_text"] = False
            return await render_template("pages/examples/forms/page.html", context=context)
    else:
        form_class = FORM_PAGE_FORMS[page]
        form_options: dict[str, Any] = {"request": request}
        if page == "tags":
            from .data_inputs import SELECT_BINDING_SECRET

            form_options["select_secret_key"] = SELECT_BINDING_SECRET
        cards: list[dict[str, Any]] = []
        if page in DUAL_MODE_FORM_PAGES:
            cards.extend(
                (
                    _form_card(form_class(prefix="html", **form_options), page, "html", _("HTML response")),
                    _form_card(form_class(prefix="json", **form_options), page, "json", _("JSON response actions")),
                )
            )
        else:
            cards.append(_form_card(form_class(prefix=page, **form_options), page, "json", context["example_page_title"]))
        context["form_cards"] = cards
        context["show_static_fields"] = page == "basics"
        context["show_slider"] = page == "sliders"
        context["show_color_picker"] = page == "color-picker"
        context["show_rich_text"] = page == "rich-text"
        template = "pages/examples/forms/containers.html" if page == "containers" else "pages/examples/forms/page.html"

    return await render_template(template, context=context)


@app.get("/examples/forms/color-picker/modal", name="example_color_picker_modal")
@add_csrf_token()
@admin_required()
async def example_color_picker_modal(request: Request):
    """Load the same ColorPicker Form through the shared dynamic Modal lifecycle."""
    form = FORM_PAGE_FORMS["color-picker"](request=request, prefix="json")
    html = await form.render(
        action="/examples/forms/color-picker/submit/json",
        form_mode="json",
        submit_label=_("Submit example"),
    )
    return json_response({"title": str(_("Color Picker in a remote Modal")), "html": str(html)})


@app.get("/examples/forms/rich-text/modal", name="example_rich_text_modal")
@add_csrf_token()
@admin_required()
async def example_rich_text_modal(request: Request):
    """Load the Rich Text Form through the shared dynamic Modal lifecycle."""
    form = FORM_PAGE_FORMS["rich-text"](request=request, prefix="json")
    html = await form.render(
        action="/examples/forms/rich-text/submit/json",
        form_mode="json",
        submit_label=_("Submit example"),
    )
    return json_response({"title": str(_("Rich Text Editor in a remote Modal")), "html": str(html)})


@app.post("/examples/forms/<page:str>/submit/<mode:str>", name="example_form_submit")
@csrf_protect()
@admin_required()
async def example_form_submit(request: Request, page: str, mode: str):
    """Validate one ordinary example Form through HTML or JSON mode."""
    form_class = FORM_PAGE_FORMS.get(page)
    if form_class is None or mode not in {"html", "json"}:
        raise NotFound("Form example submission was not found")
    if page == "multi-step":
        async with db_manager.get_session() as session:
            form = form_class.from_request(request, session=session, prefix=mode)
            if not await form.validate():
                return await _form_error_response(
                    request,
                    form,
                    action=f"/examples/forms/{page}/submit/{mode}",
                    mode=mode,
                )
            await form.save(commit=True, session=session)
    else:
        form_options: dict[str, Any] = {}
        if page == "tags":
            from .data_inputs import SELECT_BINDING_SECRET

            form_options["select_secret_key"] = SELECT_BINDING_SECRET
        form = form_class.from_request(request, prefix=mode if page in DUAL_MODE_FORM_PAGES else page, **form_options)
        if not await form.validate():
            return await _form_error_response(request, form, action=f"/examples/forms/{page}/submit/{mode}", mode=mode)
    return await _form_success_response(request, page=page, form=form)


@app.post("/examples/forms/json-list/create", name="example_stream_profile_create")
@csrf_protect()
@admin_required()
async def example_stream_profile_create(request: Request):
    """Create a real Text-backed StreamProfile from the HTML Form path."""
    async with db_manager.get_session() as session:
        form = StreamProfileForm.from_request(request, session=session, prefix="create")
        if not await form.validate():
            return await _form_error_response(request, form, action="/examples/forms/json-list/create", mode="html")
        await form.save(commit=True, session=session)
    return await _success_fragment(request, _("Stream profile created."), "/examples/forms/json-list")


@app.post("/examples/forms/json-list/<profile_id:int>/edit", name="example_stream_profile_update")
@csrf_protect()
@admin_required()
async def example_stream_profile_update(request: Request, profile_id: int):
    """Update a real StreamProfile and replace the JSON Form in place."""
    async with db_manager.get_session() as session:
        profile = await session.get(ExampleStreamProfile, profile_id)
        if profile is None:
            raise NotFound("Stream profile was not found")
        form = StreamProfileForm.from_request(request, instance=profile, session=session, prefix="edit")
        if not await form.validate():
            return json_response(form.to_api_response().to_dict(), status=200)
        saved = await form.save(commit=True, session=session)
        replacement = await StreamProfileForm(request=request, instance=saved, prefix="edit").render(
            action=f"/examples/forms/json-list/{profile_id}/edit",
            form_mode="json",
            submit_label=_("Save JSON list"),
        )

    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=_("Stream profile saved."),
        actions=[
            FeedbackAction(title=_("Stream profile saved."), icon="success"),
            ReplaceHtmlAction(target="#json-stream-profile-form", html=str(replacement)),
        ],
    )
    return json_response(payload.to_dict())


@app.get("/examples/forms/containers/modal", name="example_form_container_modal")
@add_csrf_token()
@admin_required()
async def example_form_container_modal(request: Request):
    """Load the same ordinary Form into the shared remote Modal component."""
    form = ContainerExampleForm(request=request, prefix="modal")
    html = await form.render(
        action="/examples/forms/containers/modal",
        form_mode="json",
        submit_label=_("Submit modal form"),
    )
    return json_response({"title": str(_("Remote ordinary Form")), "html": str(html)})


@app.post("/examples/forms/containers/modal", name="example_form_container_modal_submit")
@csrf_protect()
@admin_required()
async def example_form_container_modal_submit(request: Request):
    """Validate the remote Modal's ordinary Form with the shared JSON protocol."""
    form = ContainerExampleForm.from_request(request, prefix="modal")
    if not await form.validate():
        return json_response(form.to_api_response().to_dict(), status=200)
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=_("Modal form submitted."),
        actions=[FeedbackAction(title=_("Modal form submitted."), icon="success"), CloseModalAction()],
    )
    return json_response(payload.to_dict())


def _page_context(page: str) -> dict[str, Any]:
    """Build the shared Dashboard shell context for one Form page."""
    section = EXAMPLE_SECTIONS["forms"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_forms_{page.replace('-', '_')}",
        "active_section": "examples_forms",
        "example_category": "forms",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


def _form_card(form: Any, page: str, mode: str, title: object) -> dict[str, Any]:
    """Return one template-ready ordinary Form example."""
    return {
        "action": f"/examples/forms/{page}/submit/{mode}",
        "description": _("The server validates the same WTForms declaration used to render these controls."),
        "form": form,
        "mode": mode,
        "submit_label": _("Submit example"),
        "title": title,
    }


async def _form_error_response(request: Request, form: Any, *, action: str, mode: str):
    """Return the response contract selected by the mounted Form component."""
    if _accepts_json(request):
        return json_response(form.to_api_response().to_dict(), status=200)
    html = await form.render(action=action, form_mode=mode, submit_label=_("Submit example"))
    return html_response(str(html), status=422)


async def _form_success_response(request: Request, *, page: str, form: Any):
    """Return a visible success result without persisting ordinary demo input."""
    results = _submitted_results(form) if page in {"slug", "input-spinner", "tags"} else []
    html = await _render_success_fragment(
        request,
        _("Form submitted successfully."),
        f"/examples/forms/{page}",
        results=results,
    )
    if _accepts_json(request):
        actions: list[ResponseAction] = [FeedbackAction(title=_("Form submitted successfully."), icon="success")]
        if results:
            actions.append(ReplaceHtmlAction(html=html))
        payload = DefaultApiFormResponse(
            error_code=ApiErrorCode.OK,
            message=_("Form submitted successfully."),
            actions=actions,
        )
        return json_response(payload.to_dict())
    return html_response(html)


async def _success_fragment(request: Request, message: object, return_url: str):
    """Render the small HTML-mode success replacement."""
    return html_response(await _render_success_fragment(request, message, return_url))


async def _render_success_fragment(
    request: Request,
    message: object,
    return_url: str,
    *,
    results: list[dict[str, object]] | None = None,
) -> str:
    """Render reusable success HTML for both Form response modes."""
    template = request.app.ext.environment.get_template("partials/examples/forms/success.html")
    return await template.render_async(message=message, results=results or [], return_url=return_url)


def _submitted_results(form: Any) -> list[dict[str, object]]:
    """Expose server-cleaned values on examples whose result is the feature."""
    return [
        {"label": field.label.text, "name": name, "value": form.cleaned_data[name]}
        for name, field in form._fields.items()
        if name in form.cleaned_data
    ]


def _accepts_json(request: Request) -> bool:
    """Return whether the Form component requested the JSON protocol."""
    return "application/json" in str((request.headers or {}).get("accept", "")).lower()


__all__ = ["OWNED_FORM_PAGES"]
