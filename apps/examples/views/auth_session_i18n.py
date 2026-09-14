"""Executable authentication and Session examples."""

from __future__ import annotations

from config.settings import settings

from apps.auth.decorators import admin_required
from apps.auth.session import dashboard_session
from oldman.web.api import DefaultApiResponse
from oldman.web.auth import session_profile
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.session import Session
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_AUTH_PAGES = frozenset({"login", "guards", "identity"})
OWNED_SESSION_PAGES = frozenset({"lifecycle", "revoke", "expiry"})
OWNED_I18N_PAGES = frozenset({"server", "browser", "coverage"})

app = get_app()


@app.get("/examples/auth/probe", name="example_auth_probe")
@admin_required()
async def example_auth_probe(request: Request):
    """Return the identity admitted by the real Dashboard staff guard."""
    session = dashboard_session(request)
    return api_response(
        DefaultApiResponse(
            data={"user_id": session.user_id, "username": session.username}
        )
    )


@app.get("/examples/auth/<page:str>", name="example_auth_page")
@admin_required()
async def example_auth_page(request: Request, page: str):
    """Render login, guard, or current-identity behavior without duplicating Auth."""
    if page not in OWNED_AUTH_PAGES:
        return await _render_example(request, "auth", page)
    session = dashboard_session(request)
    return await render_template(
        f"pages/examples/auth/{page}.html",
        context={
            **_page_context("auth", page),
            "session": session,
            "session_profile": session_profile(session),
        },
    )


@app.get("/examples/session/<page:str>", name="example_session_page")
@add_csrf_token()
@admin_required()
async def example_session_page(request: Request, page: str):
    """Render the current Redis-backed Session lifecycle."""
    if page not in OWNED_SESSION_PAGES:
        return await _render_example(request, "session", page)
    session = dashboard_session(request)
    context = {
        **_page_context("session", page),
        "session": session,
        "session_profile": session_profile(session),
        "session_expiry": settings.web.session.expiry,
    }
    if page == "lifecycle":
        manager = Session.get_session_manager(request)
        assert session.user_id is not None
        context["active_session_count"] = len(
            await manager.get_active_session_ids(session.user_id)
        )
    return await render_template(
        f"pages/examples/session/{page}.html",
        context=context,
    )


@app.post("/examples/session/revoke/submit", name="example_session_revoke")
@csrf_protect()
@admin_required()
async def example_session_revoke(request: Request):
    """Revoke every current-user Session and let the shared SSE guard report it."""
    session = dashboard_session(request)
    assert session.user_id is not None
    manager = Session.get_session_manager(request)
    revoked = await manager.force_logout_user(session.user_id)
    return api_response(DefaultApiResponse(data={"revoked": len(revoked)}))


@app.post("/examples/session/expiry/submit", name="example_session_expire")
@csrf_protect()
@admin_required()
async def example_session_expire(request: Request):
    """Remove the current Redis record so the shared SSE expiry UI can be observed."""
    manager = Session.get_session_manager(request)
    await manager.logout(manager.get_session_id(request))
    return api_response(DefaultApiResponse())


@app.get("/examples/i18n/<page:str>", name="example_i18n_page")
@admin_required()
async def example_i18n_page(request: Request, page: str):
    """Render the shared server and browser translation pipeline."""
    if page not in OWNED_I18N_PAGES:
        return await _render_example(request, "i18n", page)
    return await render_template(
        f"pages/examples/i18n/{page}.html",
        context=_page_context("i18n", page),
    )


def _page_context(category: str, page: str) -> dict[str, object]:
    """Build the shared examples shell context for one Auth or Session page."""
    section = EXAMPLE_SECTIONS[category]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_{category}_{page}",
        "active_section": f"examples_{category}",
        "example_category": category,
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = [
    "OWNED_AUTH_PAGES",
    "OWNED_I18N_PAGES",
    "OWNED_SESSION_PAGES",
    "example_auth_page",
    "example_auth_probe",
    "example_i18n_page",
    "example_session_expire",
    "example_session_page",
    "example_session_revoke",
]
