"""Executable authentication and Session examples."""

from __future__ import annotations

import inspect

from oldman.db import db_manager
from oldman.web import router
from oldman.web.api import DefaultApiResponse
from oldman.web.auth import authenticated_by, login_required, session_profile, staff_required
from oldman.web.authentication import API_KEY_HEADER, API_KEY_METHOD, JWT_METHOD
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.session import Session
from oldman.web.template import render_template
from sqlalchemy import select

from apps.examples.models import ExampleTeam
from apps.examples.session import dashboard_session
from apps.examples.tables import TeamProjectTable
from config.settings import settings

from . import EXAMPLE_SECTIONS, _render_example

OWNED_AUTH_PAGES = frozenset({"login", "guards", "identity", "tokens", "callers"})
OWNED_SESSION_PAGES = frozenset({"lifecycle", "revoke", "expiry"})
OWNED_I18N_PAGES = frozenset({"server", "browser", "coverage"})

# The Access Guards page's class-based example: its data endpoint, guarded by the table's own hooks.
router.add_route(TeamProjectTable.as_view(), TeamProjectTable.route_path, name=TeamProjectTable.route_name)



@router.get("/examples/auth/probe", name="example_auth_probe")
@staff_required()
async def example_auth_probe(request: Request):
    """Return the user the staff guard admitted, and how the request authenticated."""
    return api_response(
        DefaultApiResponse(
            data={
                "user_id": request.ctx.user.id,
                "username": request.ctx.user.username,
                "method": request.ctx.auth.method,
            }
        )
    )


@router.get("/examples/auth/service-probe", name="example_service_probe")
@authenticated_by(API_KEY_METHOD)
async def example_service_probe(request: Request):
    """Answer a program that sent an API key: which key it was, with no user signed in."""
    return api_response(
        DefaultApiResponse(
            data={
                "caller": request.ctx.auth.caller,
                "method": request.ctx.auth.method,
                "user_id": request.ctx.user.id,
            }
        )
    )


@router.get("/examples/auth/<page:str>", name="example_auth_page")
@login_required()
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
            **_caller_context(request, page),
            **(await _guards_context(request) if page == "guards" else {}),
        },
    )


@router.get("/examples/session/<page:str>", name="example_session_page")
@add_csrf_token()
@login_required()
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


@router.post("/examples/session/revoke/submit", name="example_session_revoke")
@csrf_protect()
@login_required()
async def example_session_revoke(request: Request):
    """Revoke every current-user Session and let the shared SSE guard report it."""
    session = dashboard_session(request)
    assert session.user_id is not None
    manager = Session.get_session_manager(request)
    revoked = await manager.force_logout_user(session.user_id)
    return api_response(DefaultApiResponse(data={"revoked": len(revoked)}))


@router.post("/examples/session/expiry/submit", name="example_session_expire")
@csrf_protect()
@login_required()
async def example_session_expire(request: Request):
    """Remove the current Redis record so the shared SSE expiry UI can be observed."""
    manager = Session.get_session_manager(request)
    await manager.logout(manager.get_session_id(request))
    return api_response(DefaultApiResponse())


@router.get("/examples/i18n/<page:str>", name="example_i18n_page")
@login_required()
async def example_i18n_page(request: Request, page: str):
    """Render the shared server and browser translation pipeline."""
    if page not in OWNED_I18N_PAGES:
        return await _render_example(request, "i18n", page)
    return await render_template(
        f"pages/examples/i18n/{page}.html",
        context=_page_context("i18n", page),
    )


async def _guards_context(request: Request) -> dict[str, object]:
    """The team the class-based example shows (`?team=`, the first team by default) and every team to pick from.

    Inactive teams are listed too: choosing one is how the page shows check_auth refusing.
    """
    async with db_manager.get_read_session() as session:
        teams = list((await session.scalars(select(ExampleTeam).order_by(ExampleTeam.id))).all())
    requested = str(request.args.get("team", ""))
    selected = next((team for team in teams if str(team.id) == requested), teams[0] if teams else None)
    return {
        "guard_teams": teams,
        "guard_team": selected,
        "team_project_table": TeamProjectTable(request=request),
        # The page shows the class itself, so what it explains is always the code that runs.
        "team_project_table_source": inspect.getsource(TeamProjectTable),
    }


def _caller_context(request: Request, page: str) -> dict[str, object]:
    """What the token and service-caller pages show: whether the method is on, and where to call.

    Only key names reach the page, never a secret: the page sends the key the reader pastes in.
    """
    enabled = settings.web.auth.authenticators or ()
    if page == "tokens":
        return {
            "jwt_enabled": JWT_METHOD in enabled,
            "token_obtain_url": request.app.url_for("token_obtain"),
            "token_refresh_url": request.app.url_for("token_refresh"),
            "token_revoke_url": request.app.url_for("token_revoke"),
            "probe_url": request.app.url_for("example_auth_probe"),
        }
    if page == "callers":
        return {
            "api_key_enabled": API_KEY_METHOD in enabled,
            "api_key_names": sorted(settings.web.auth.api_keys),
            "api_key_header": API_KEY_HEADER,
            "service_probe_url": request.app.url_for("example_service_probe"),
            "service_probe_absolute_url": settings.web.domain.rstrip("/") + request.app.url_for("example_service_probe"),
        }
    return {}


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
    "example_service_probe",
    "example_session_expire",
    "example_session_page",
    "example_session_revoke",
]
