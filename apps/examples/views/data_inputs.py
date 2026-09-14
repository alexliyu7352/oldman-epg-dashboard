"""Real database-backed List, Select and Autocomplete examples."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from apps.auth.decorators import admin_required
from apps.examples import providers as _providers  # noqa: F401 - import registers providers.
from apps.examples.forms import LogoAutocompleteForm, LogoMultipleSelectForm, LogoSelectForm
from apps.examples.models import ExampleLogo, ExampleProject, ExampleStreamProfile
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound
from oldman.web.api import ApiErrorCode, DefaultApiFormResponse, FeedbackAction, ReplaceHtmlAction
from oldman.web.components.selects import SelectProviderView
from oldman.web.request import Request
from oldman.web.response import json_response
from oldman.web.routing import get_app
from oldman.web.security import WebSecurityPurpose, configured_web_security_key
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

SELECT_BINDING_SECRET = configured_web_security_key(WebSecurityPurpose.SELECT_BINDING)

app = get_app()
app.add_route(
    SelectProviderView.as_view(secret_key=SELECT_BINDING_SECRET),
    "/examples/select/<provider_name:str>",
    name="example_select_provider",
)


@app.get("/examples/data-inputs/<page:str>", name="example_data_inputs_page")
@add_csrf_token()
@admin_required()
async def example_data_inputs_page(request: Request, page: str):
    """Render the three data-input pages from real fixture rows."""
    if page not in {"lists", "autocomplete", "providers"}:
        raise NotFound("Data input example page was not found")
    context = _page_context("data-inputs", page)
    if page == "lists":
        async with db_manager.get_read_session() as session:
            result = await session.execute(
                select(ExampleProject)
                .options(selectinload(ExampleProject.team))
                .order_by(ExampleProject.id.asc())
                .limit(24)
            )
            context["projects"] = list(result.scalars())
    elif page == "autocomplete":
        profile, logo = await _first_profile_with_logo()
        context.update(profile=profile, logo=logo, autocomplete_form=_autocomplete_form(request, profile, logo))
    else:
        async with db_manager.get_read_session() as session:
            result = await session.execute(select(ExampleLogo).order_by(ExampleLogo.id.asc()).limit(12))
            logos = list(result.scalars())
            context.update(
                logos=logos,
                multiple_form=LogoMultipleSelectForm(
                    request=request,
                    initial_logos=tuple(logos[:2]),
                    select_secret_key=SELECT_BINDING_SECRET,
                ),
            )
    return await render_template(f"pages/examples/data_inputs/{page}.html", context=context)


async def render_remote_form_page(request: Request, page: str, context: dict[str, Any]):
    """Render the Forms section's concrete Select or Autocomplete page."""
    profile, logo = await _first_profile_with_logo()
    context.update(profile=profile, logo=logo)
    if page == "selects":
        async with db_manager.get_read_session() as session:
            result = await session.execute(select(ExampleLogo).order_by(ExampleLogo.id.asc()).limit(2))
            initial_logos = tuple(result.scalars())
        context.update(
            form=_select_form(request, profile, logo),
            multiple_form=LogoMultipleSelectForm(
                request=request,
                initial_logos=initial_logos,
                select_secret_key=SELECT_BINDING_SECRET,
            ),
        )
    else:
        context["form"] = _autocomplete_form(request, profile, logo)
    return await render_template(f"pages/examples/forms/{page}.html", context=context)


@app.post("/examples/forms/selects/<profile_id:int>/edit", name="example_logo_select_update")
@csrf_protect()
@admin_required()
async def example_logo_select_update(request: Request, profile_id: int):
    """Persist a provider-selected Logo foreign key."""
    return await _update_logo(request, profile_id, mode="selects")


@app.post("/examples/forms/autocomplete/<profile_id:int>/edit", name="example_logo_autocomplete_update")
@csrf_protect()
@admin_required()
async def example_logo_autocomplete_update(request: Request, profile_id: int):
    """Persist an autocomplete-selected Logo foreign key."""
    return await _update_logo(request, profile_id, mode="autocomplete")


@app.post("/examples/data-inputs/autocomplete/<profile_id:int>/edit", name="example_data_input_autocomplete_update")
@csrf_protect()
@admin_required()
async def example_data_input_autocomplete_update(request: Request, profile_id: int):
    """Use the same autocomplete contract from the Data inputs section."""
    return await _update_logo(request, profile_id, mode="data-inputs-autocomplete")


async def _update_logo(request: Request, profile_id: int, *, mode: str):
    """Validate and save one Logo selection, then replace the same ordinary Form."""
    form_class = LogoSelectForm if mode == "selects" else LogoAutocompleteForm
    action = {
        "selects": f"/examples/forms/selects/{profile_id}/edit",
        "autocomplete": f"/examples/forms/autocomplete/{profile_id}/edit",
        "data-inputs-autocomplete": f"/examples/data-inputs/autocomplete/{profile_id}/edit",
    }[mode]
    target = "#example-logo-select-form" if mode == "selects" else "#example-logo-autocomplete-form"
    async with db_manager.get_session() as session:
        profile = await session.get(ExampleStreamProfile, profile_id)
        if profile is None:
            raise NotFound("Stream profile was not found")
        form = form_class.from_request(
            request,
            instance=profile,
            session=session,
            select_secret_key=SELECT_BINDING_SECRET,
        )
        if not await form.validate():
            return json_response(form.to_api_response().to_dict())
        saved = await form.save(commit=True, session=session)
        logo = await session.get(ExampleLogo, saved.logo_id)
        replacement = form_class(
            request=request,
            instance=saved,
            initial_logo=logo,
            select_secret_key=SELECT_BINDING_SECRET,
        )
        html = await replacement.render(action=action, form_mode="json", submit_label=_("Save logo"), validate=True)

    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=_("Logo selection saved."),
        actions=[
            FeedbackAction(title=_("Logo selection saved."), icon="success"),
            ReplaceHtmlAction(target=target, html=str(html)),
        ],
    )
    return json_response(payload.to_dict())


async def _first_profile_with_logo() -> tuple[ExampleStreamProfile | None, ExampleLogo | None]:
    """Return the stable first fixture profile and its current Logo."""
    async with db_manager.get_read_session() as session:
        result = await session.execute(select(ExampleStreamProfile).order_by(ExampleStreamProfile.id.asc()).limit(1))
        profile = result.scalar_one_or_none()
        logo = await session.get(ExampleLogo, profile.logo_id) if profile and profile.logo_id else None
        return profile, logo


def _select_form(request: Request, profile: ExampleStreamProfile | None, logo: ExampleLogo | None) -> LogoSelectForm | None:
    """Build the real edit Select form when fixture data exists."""
    if profile is None:
        return None
    return LogoSelectForm(
        request=request,
        instance=profile,
        initial_logo=logo,
        select_secret_key=SELECT_BINDING_SECRET,
    )


def _autocomplete_form(request: Request, profile: ExampleStreamProfile | None, logo: ExampleLogo | None) -> LogoAutocompleteForm | None:
    """Build the real edit Autocomplete form when fixture data exists."""
    if profile is None:
        return None
    return LogoAutocompleteForm(
        request=request,
        instance=profile,
        initial_logo=logo,
        select_secret_key=SELECT_BINDING_SECRET,
    )


def _page_context(category: str, page: str) -> dict[str, Any]:
    """Build the standard examples shell context."""
    section = EXAMPLE_SECTIONS[category]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_{category.replace('-', '_')}_{page.replace('-', '_')}",
        "active_section": f"examples_{category.replace('-', '_')}",
        "example_category": category,
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["example_data_inputs_page", "render_remote_form_page"]
