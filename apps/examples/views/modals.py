"""Executable Modal and ordered response Action examples."""

from __future__ import annotations

from oldman.i18n import LazyTranslation
from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound, router
from oldman.web.api import (
    CloseModalAction,
    DefaultApiResponse,
    FeedbackAction,
    RedirectAction,
    ReloadTableAction,
    ReplaceHtmlAction,
    ResponseAction,
    feedback_response,
    form_response,
    modal_response,
)
from oldman.web.auth import staff_required
from oldman.web.request import Request
from oldman.web.response import api_response
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_fragment, render_template

from apps.examples.forms import ContainerExampleForm
from apps.examples.tables import ExampleProjectTable

from . import EXAMPLE_SECTIONS, _render_example

OWNED_MODAL_PAGES = frozenset({"basics", "remote", "workflows", "actions"})


class _ExampleMarkAction(ResponseAction, tag="example_mark", kw_only=True):
    """Update one Demo-owned output through the Page extension hook."""

    text: str | LazyTranslation


class _ExampleUnknownAction(ResponseAction, tag="example_unknown", kw_only=True):
    """Exercise the public unknown-Action failure path."""




@router.get("/examples/modals/<page:str>", name="example_modals_page")
@add_csrf_token()
@staff_required()
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


@router.get("/examples/modals/remote/parts/<step:int>", name="example_modal_remote_part")
@staff_required()
async def example_modal_remote_part(request: Request, step: int):
    """Return replaceable title, body and footer parts for one open Modal."""
    if step not in {1, 2}:
        raise NotFound("Remote Modal step was not found")
    return modal_response(
        _("Remote content: first step") if step == 1 else _("Remote content: expanded step"),
        body=await _render_partial(request, "remote_step.html", step=step),
        footer=await _render_partial(request, "remote_footer.html", step=step),
    )


@router.get("/examples/modals/workflow/parts/<step:int>", name="example_modal_workflow_part")
@add_csrf_token()
@staff_required()
async def example_modal_workflow_part(request: Request, step: int):
    """Load one ordinary JSON Form as the current workflow step."""
    if step not in {1, 2}:
        raise NotFound("Modal workflow step was not found")
    return modal_response(
        _("Modal workflow"),
        body=await _workflow_form(request, step),
        footer=_("The same ordinary Form component handles both steps."),
    )


@router.post("/examples/modals/workflow/<step:int>", name="example_modal_workflow_submit")
@csrf_protect()
@staff_required()
async def example_modal_workflow_submit(request: Request, step: int):
    """Validate a workflow step, replace the Form, then close on completion."""
    if step not in {1, 2}:
        raise NotFound("Modal workflow step was not found")
    form = ContainerExampleForm.from_request(request)
    if not await form.validate():
        return api_response(form.to_api_response())
    if step == 1:
        return form_response(_("First step completed."), actions=[ReplaceHtmlAction(html=str(await _workflow_form(request, 2)))])
    return feedback_response(_("Modal workflow completed."), actions=[CloseModalAction()])


@router.get("/examples/modals/actions/feedback", name="example_action_feedback")
@staff_required()
async def example_action_feedback(request: Request):
    """Return the built-in Feedback Action."""
    del request
    return api_response(DefaultApiResponse(actions=[FeedbackAction(title=_("Feedback Action completed."), icon="success")]))


@router.get("/examples/modals/actions/replace-html", name="example_action_replace_html")
@staff_required()
async def example_action_replace_html(request: Request):
    """Let the trigger choose where a target-less Replace HTML Action renders."""
    del request
    return api_response(DefaultApiResponse(actions=[ReplaceHtmlAction(html=str(_("HTML replaced by an ordered Action.")))]))


@router.get("/examples/modals/actions/close-modal", name="example_action_close_modal")
@staff_required()
async def example_action_close_modal(request: Request):
    """Close the Modal nearest to the Action source."""
    del request
    return api_response(DefaultApiResponse(actions=[CloseModalAction()]))


@router.get("/examples/modals/actions/reload-table", name="example_action_reload_table")
@staff_required()
async def example_action_reload_table(request: Request):
    """Reload only the explicitly targeted mounted Table."""
    del request
    return api_response(DefaultApiResponse(actions=[ReloadTableAction(target="#example-projects-table")]))


@router.get("/examples/modals/actions/redirect", name="example_action_redirect")
@staff_required()
async def example_action_redirect(request: Request):
    """Terminate the Action chain with a real browser navigation."""
    del request
    return api_response(DefaultApiResponse(actions=[RedirectAction(url="/examples/modals/actions?redirected=1")]))


@router.get("/examples/modals/actions/private", name="example_action_private")
@staff_required()
async def example_action_private(request: Request):
    """Return one Demo-owned Action handled by ExamplesPage."""
    del request
    return api_response(
        DefaultApiResponse(actions=[_ExampleMarkAction(target="#private-action-result", text=_("Private Page Action completed."))])
    )


@router.get("/examples/modals/actions/missing-target", name="example_action_missing_target")
@staff_required()
async def example_action_missing_target(request: Request):
    """Exercise visible failure when an explicit target is absent."""
    del request
    return api_response(
        DefaultApiResponse(actions=[ReplaceHtmlAction(target="#missing-action-target", html="never rendered")])
    )


@router.get("/examples/modals/actions/unknown", name="example_action_unknown")
@staff_required()
async def example_action_unknown(request: Request):
    """Exercise visible failure for an Action no Page owns."""
    del request
    return api_response(DefaultApiResponse(actions=[_ExampleUnknownAction()]))


@router.get("/examples/modals/actions/chain-failure", name="example_action_chain_failure")
@staff_required()
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
    return await render_fragment(request, f"partials/examples/modals/{name}", **context)


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
