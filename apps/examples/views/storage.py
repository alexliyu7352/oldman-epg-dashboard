"""Real Upload, model file lifecycle and Storage API examples."""

from __future__ import annotations

from apps.auth.decorators import admin_required
from apps.examples import services
from apps.examples.forms import AssetRollbackForm, ExampleAssetForm
from apps.examples.models import ExampleAsset
from oldman.db import db_manager
from oldman.i18n import gettext_lazy as _
from oldman.storage import storages
from oldman.web import NotFound
from oldman.web.api import ApiErrorCode, DefaultApiFormResponse, FeedbackAction, RedirectAction, ReplaceHtmlAction
from oldman.web.components.forms import TailwindForm
from oldman.web.request import Request
from oldman.web.response import json_response
from oldman.web.routing import get_app
from oldman.web.security.csrf import add_csrf_token, csrf_protect
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

app = get_app()


class _ExpectedRollback(Exception):
    """Stop the Demo transaction after a real ModelForm file save."""


@app.get("/examples/storage/<page:str>", name="example_storage_page")
@add_csrf_token()
@admin_required()
async def example_storage_page(request: Request, page: str):
    """Render one concrete Storage example page."""
    if page == "upload":
        return await render_upload_page(request, category="storage")
    if page == "lifecycle":
        return await _render_lifecycle_page(request)
    if page == "api":
        context = _page_context("storage", "api")
        context.update(api_form=TailwindForm(request=request, prefix="storage-api"), api_result=None)
        return await render_template("pages/examples/storage/api.html", context=context)
    raise NotFound("Storage example page was not found")


async def render_upload_page(request: Request, *, category: str):
    """Render the same real upload form from Forms and Storage navigation."""
    context = _page_context(category, "upload")
    context.update(assets=await services.list_assets(), form=ExampleAssetForm(request=request))
    template = "pages/examples/forms/upload.html" if category == "forms" else "pages/examples/storage/upload.html"
    return await render_template(template, context=context)


@app.post("/examples/storage/assets/create", name="example_asset_create")
@csrf_protect()
@admin_required()
async def example_asset_create(request: Request):
    """Save both uploaded fields through the framework write session."""
    async with db_manager.get_session() as session:
        form = ExampleAssetForm.from_request(request, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict())
        asset = await form.save(commit=True, session=session)
        asset_id = int(asset.id)
    return _redirect_response(_("Asset uploaded."), f"/examples/storage/lifecycle?asset={asset_id}")


@app.post("/examples/storage/assets/<asset_id:int>/update", name="example_asset_update")
@csrf_protect()
@admin_required()
async def example_asset_update(request: Request, asset_id: int):
    """Replace only files submitted by the edit Form and keep missing uploads."""
    async with db_manager.get_session() as session:
        asset = await _asset_or_404(session, asset_id)
        form = ExampleAssetForm.from_request(request, instance=asset, session=session)
        if not await form.validate():
            return json_response(form.to_api_response().to_dict())
        await form.save(commit=True, session=session)
    return _redirect_response(_("Asset saved."), f"/examples/storage/lifecycle?asset={asset_id}")


@app.post("/examples/storage/assets/<asset_id:int>/clear-preview", name="example_asset_clear_preview")
@csrf_protect()
@admin_required()
async def example_asset_clear_preview(request: Request, asset_id: int):
    """Explicitly clear the nullable preview field through the model lifecycle."""
    del request
    async with db_manager.get_session() as session:
        asset = await _asset_or_404(session, asset_id)
        asset.preview_path = None
    return _redirect_response(_("Preview cleared."), f"/examples/storage/lifecycle?asset={asset_id}")


@app.post("/examples/storage/assets/<asset_id:int>/delete", name="example_asset_delete")
@csrf_protect()
@admin_required()
async def example_asset_delete(request: Request, asset_id: int):
    """Delete the model so the framework cleans both unreferenced files."""
    del request
    async with db_manager.get_session() as session:
        asset = await _asset_or_404(session, asset_id)
        await session.delete(asset)
    return _redirect_response(_("Asset deleted."), "/examples/storage/upload")


@app.post("/examples/storage/assets/<asset_id:int>/rollback", name="example_asset_rollback")
@csrf_protect()
@admin_required()
async def example_asset_rollback(request: Request, asset_id: int):
    """Roll back one real replacement and prove its newly stored file is cleaned."""
    candidate = ""
    try:
        async with db_manager.get_session() as session:
            asset = await _asset_or_404(session, asset_id)
            form = AssetRollbackForm.from_request(request, instance=asset, session=session, prefix="rollback")
            if not await form.validate():
                return json_response(form.to_api_response().to_dict())
            saved = await form.save(commit=True, session=session)
            candidate = str(saved.preview_path or "")
            raise _ExpectedRollback
    except _ExpectedRollback:
        pass

    if candidate and await storages.using("default").exists(candidate):
        raise RuntimeError("Rolled-back ExampleAsset file was not cleaned")
    return _feedback_response(_("Transaction rolled back and the temporary file was removed."))


@app.post("/examples/storage/api/run", name="example_storage_api_run")
@csrf_protect()
@admin_required()
async def example_storage_api_run(request: Request):
    """Run the fixed-prefix Storage API sequence and replace its result panel."""
    result = await services.run_storage_api_demo()
    template = request.app.ext.environment.get_template("pages/examples/storage/_api_result.html")
    html = await template.render_async(result=result)
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=_("Storage API sequence completed."),
        actions=[
            ReplaceHtmlAction(target="#storage-api-result", html=html),
            FeedbackAction(title=_("Storage API sequence completed."), icon="success"),
        ],
    )
    return json_response(payload.to_dict())


async def _render_lifecycle_page(request: Request):
    """Render recent assets and the selected record's live Storage state."""
    assets = await services.list_assets()
    raw_asset_id = request.args.get("asset")
    asset_id = int(raw_asset_id) if raw_asset_id and str(raw_asset_id).isdigit() else None
    selected = next((asset for asset in assets if int(asset.id) == asset_id), assets[0] if assets else None)
    states = await services.asset_file_states(selected) if selected is not None else {}
    context = _page_context("storage", "lifecycle")
    context.update(
        assets=assets,
        file_states=states,
        form=ExampleAssetForm(request=request, instance=selected) if selected is not None else None,
        rollback_asset_form=AssetRollbackForm(request=request, instance=selected, prefix="rollback")
        if selected is not None
        else None,
        selected_asset=selected,
    )
    return await render_template("pages/examples/storage/lifecycle.html", context=context)


async def _asset_or_404(session, asset_id: int) -> ExampleAsset:
    """Return one ExampleAsset from the active write transaction."""
    asset = await session.get(ExampleAsset, asset_id)
    if asset is None:
        raise NotFound("Example asset was not found")
    return asset


def _page_context(category: str, page: str) -> dict[str, object]:
    """Build the shared examples shell context for a Storage-owned page."""
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


def _redirect_response(message: object, url: str):
    """Return one successful Form response followed by a full page reload."""
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=message,
        actions=[FeedbackAction(title=message, icon="success"), RedirectAction(url=url)],
    )
    return json_response(payload.to_dict())


def _feedback_response(message: object):
    """Return one successful Form response that keeps the current page visible."""
    payload = DefaultApiFormResponse(
        error_code=ApiErrorCode.OK,
        message=message,
        actions=[FeedbackAction(title=message, icon="success")],
    )
    return json_response(payload.to_dict())


__all__ = ["example_storage_page", "render_upload_page"]
