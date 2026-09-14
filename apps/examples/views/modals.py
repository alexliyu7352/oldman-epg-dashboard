"""Executable Modal and ordered response Action examples."""

from __future__ import annotations

from apps.auth.decorators import admin_required
from apps.examples.forms import ContainerExampleForm
from apps.examples.tables import ExampleProjectTable
from oldman.i18n import LazyTranslation
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound
from oldman.web.api import (
    ApiErrorCode,
    CloseModalAction,
    DefaultApiFormResponse,
    DefaultApiResponse,
    FeedbackAction,
    RedirectAction,
    ReloadTableAction,
    ReplaceHtmlAction,
    ResponseAction,
)
from oldman.web.request import Request
from oldman.web.response import api_response, json_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS, _render_example

OWNED_MODAL_PAGES = frozenset({"basics", "remote", "workflows", "actions"})


class _ExampleMarkAction(ResponseAction, tag="example_mark", kw_only=True):
    """Update one Demo-owned output through the Page extension hook."""

    text: str | LazyTranslation


class _ExampleUnknownAction(ResponseAction, tag="example_unknown", kw_only=True):
    """Exercise the public unknown-Action failure path."""


app = get_app()


@app.get("/examples/modals/<page:str>", name="example_modals_page")
@add_csrf_token()
@admin_required()
async def example_modals_page(request: Request, page: str):
    """Render one concrete Modal or response Action example page."""
    if page not in OWNED_MODAL_PAGES:
        return await _render_example(request, "modals", page)
    context = _page_context(page)
    if page == "workflows":
        context["inline_form"] = ContainerExampleForm(request=request, prefix="containers")
    if page == "actions":
        context.update(
            redirected=request.args.get("redirected") == "1",
            table=ExampleProjectTable(request=request),
        )
    return await render_template(f"pages/examples/modals/{page}.html", context=context)


@app.get("/examples/modals/remote/parts/<step:int>", name="example_modal_remote_part")
@admin_required()
async def example_modal_remote_part(request: Request, step: int):
    """Return replaceable title, body and footer parts for one open Modal."""
    if step not in {1, 2}:
        raise NotFound("Remote Modal step was not found")
    return json_response(
        {
            "title": str(_("Remote content: first step") if step == 1 else _("Remote content: expanded step")),
            "body": await _render_partial(request, "remote_step.html", step=step),
            "footer": await _render_partial(request, "remote_footer.html", step=step),
        }
    )


@app.get("/examples/modals/workflow/parts/<step:int>", name="example_modal_workflow_part")
@add_csrf_token()
@admin_required()
async def example_modal_workflow_part(request: Request, step: int):
    """Load one ordinary JSON Form as the current workflow step."""
    if step not in {1, 2}:
        raise NotFound("Modal workflow step was not found")
    return json_response(
        {
            "title": str(_("Modal workflow")),
            "body": str(await _workflow_form(request, step)),
            "footer": str(_("The same ordinary Form component handles both steps.")),
        }
    )


@app.post("/examples/modals/workflow/<step:int>", name="example_modal_workflow_submit")
@csrf_protect()
@admin_required()
async def example_modal_workflow_submit(request: Request, step: int):
    """Validate a workflow step, replace the Form, then close on completion."""
    if step not in {1, 2}:
        raise NotFound("Modal workflow step was not found")
    form = ContainerExampleForm.from_request(request)
    if not await form.validate():
        return api_response(form.to_api_response())
    if step == 1:
        payload = DefaultApiFormResponse(
            error_code=ApiErrorCode.OK,
            message=_("First step completed."),
            actions=[ReplaceHtmlAction(html=str(await _workflow_form(request, 2)))],
        )
    else:
        payload = DefaultApiFormResponse(
            error_code=ApiErrorCode.OK,
            message=_("Modal workflow completed."),
            actions=[
                FeedbackAction(title=_("Modal workflow completed."), icon="success"),
                CloseModalAction(),
            ],
        )
    return api_response(payload)


@app.get("/examples/modals/actions/feedback", name="example_action_feedback")
@admin_required()
async def example_action_feedback(request: Request):
    """Return the built-in Feedback Action."""
    del request
    return api_response(DefaultApiResponse(actions=[FeedbackAction(title=_("Feedback Action completed."), icon="success")]))


@app.get("/examples/modals/actions/replace-html", name="example_action_replace_html")
@admin_required()
async def example_action_replace_html(request: Request):
    """Let the trigger choose where a target-less Replace HTML Action renders."""
    del request
    return api_response(DefaultApiResponse(actions=[ReplaceHtmlAction(html=str(_("HTML replaced by an ordered Action.")))]))


@app.get("/examples/modals/actions/close-modal", name="example_action_close_modal")
@admin_required()
async def example_action_close_modal(request: Request):
    """Close the Modal nearest to the Action source."""
    del request
    return api_response(DefaultApiResponse(actions=[CloseModalAction()]))


@app.get("/examples/modals/actions/reload-table", name="example_action_reload_table")
@admin_required()
async def example_action_reload_table(request: Request):
    """Reload only the explicitly targeted mounted Table."""
    del request
    return api_response(DefaultApiResponse(actions=[ReloadTableAction(target="#example-projects-table")]))


@app.get("/examples/modals/actions/redirect", name="example_action_redirect")
@admin_required()
async def example_action_redirect(request: Request):
    """Terminate the Action chain with a real browser navigation."""
    del request
    return api_response(DefaultApiResponse(actions=[RedirectAction(url="/examples/modals/actions?redirected=1")]))


@app.get("/examples/modals/actions/private", name="example_action_private")
@admin_required()
async def example_action_private(request: Request):
    """Return one Demo-owned Action handled by ExamplesPage."""
    del request
    return api_response(
        DefaultApiResponse(actions=[_ExampleMarkAction(target="#private-action-result", text=_("Private Page Action completed."))])
    )


@app.get("/examples/modals/actions/missing-target", name="example_action_missing_target")
@admin_required()
async def example_action_missing_target(request: Request):
    """Exercise visible failure when an explicit target is absent."""
    del request
    return api_response(
        DefaultApiResponse(actions=[ReplaceHtmlAction(target="#missing-action-target", html="never rendered")])
    )


@app.get("/examples/modals/actions/unknown", name="example_action_unknown")
@admin_required()
async def example_action_unknown(request: Request):
    """Exercise visible failure for an Action no Page owns."""
    del request
    return api_response(DefaultApiResponse(actions=[_ExampleUnknownAction()]))


@app.get("/examples/modals/actions/chain-failure", name="example_action_chain_failure")
@admin_required()
async def example_action_chain_failure(request: Request):
    """Prove a failed middle Action stops every later Action."""
    del request
    return api_response(
        DefaultApiResponse(
            actions=[
                _ExampleMarkAction(target="#action-chain-result", text=_("First Action completed.")),
                ReplaceHtmlAction(target="#missing-action-target", html="never rendered"),
                _ExampleMarkAction(target="#action-chain-result", text="later Action must not run"),
            ]
        )
    )


async def _workflow_form(request: Request, step: int):
    """Render the ordinary Form used by both workflow steps."""
    form = ContainerExampleForm(request=request)
    return await form.render(
        action=f"/examples/modals/workflow/{step}",
        form_mode="json",
        submit_label=_("Continue") if step == 1 else _("Complete workflow"),
    )


async def _render_partial(request: Request, name: str, **context: object) -> str:
    """Render one fixed remote Modal fragment through the configured environment."""
    template = request.app.ext.environment.get_template(f"partials/examples/modals/{name}")
    return await template.render_async(**context)


def _page_context(page: str) -> dict[str, object]:
    """Build the shared examples shell context for one Modal page."""
    section = EXAMPLE_SECTIONS["modals"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return {
        "active_page": f"examples_modals_{page.replace('-', '_')}",
        "active_section": "examples_modals",
        "example_category": "modals",
        "example_page": page,
        "example_page_title": pages[page],
        "example_section": section,
        "page_entry": "examples",
    }


__all__ = ["OWNED_MODAL_PAGES", "example_modals_page"]
