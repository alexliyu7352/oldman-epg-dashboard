"""Route, guard and navigation contracts for the Dashboard examples shell."""

from __future__ import annotations

import asyncio
import inspect
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from apps.auth.session import DashboardSessionData
from jinja2 import Environment
from oldman.web.session import Session
from tests.test_web_app import create_test_app


ROOT = Path(__file__).resolve().parents[1]


def request_with_session(session: DashboardSessionData, path: str):
    """Build the request surface consumed by the shared staff guard."""
    session_manager = Session()
    session_manager.interface = cast(Any, SimpleNamespace(session_name="session"))
    return SimpleNamespace(
        args={},
        app=SimpleNamespace(ctx=SimpleNamespace(session=session_manager)),
        ctx=SimpleNamespace(session=session),
        headers={"accept": "text/html"},
        path=path,
        query_string="",
    )


class ExampleRouteTests(unittest.TestCase):
    """Keep all documented example paths on one guarded Page entry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_test_app()

    def test_example_routes_and_sidebar_cover_every_documented_page(self) -> None:
        from apps.examples.views import EXAMPLE_SECTIONS

        route_names = set(self.app.router.name_index)
        self.assertIn(f"{self.app.name}.examples_index", route_names)
        self.assertIn(f"{self.app.name}.examples_page", route_names)
        self.assertIn(f"{self.app.name}.examples_plugins", route_names)
        for name in (
            "example_http_page",
            "example_http_operation",
            "example_forms_page",
            "example_form_submit",
            "example_stream_profile_create",
            "example_stream_profile_update",
            "example_form_container_modal",
            "example_data_inputs_page",
            "example_select_provider",
            "example_logo_select_update",
            "example_logo_autocomplete_update",
            "example_storage_page",
            "example_asset_create",
            "example_asset_update",
            "example_asset_clear_preview",
            "example_asset_delete",
            "example_asset_rollback",
            "example_storage_api_run",
            "example_tables_page",
            "example_projects_table",
            "example_project_create_modal",
            "example_project_create",
            "example_project_edit_modal",
            "example_project_update",
            "example_project_delete_modal",
            "example_project_delete",
            "example_modals_page",
            "example_modal_remote_part",
            "example_modal_workflow_part",
            "example_modal_workflow_submit",
            "example_action_feedback",
            "example_action_replace_html",
            "example_action_close_modal",
            "example_action_reload_table",
            "example_action_redirect",
            "example_action_private",
            "example_action_missing_target",
            "example_action_unknown",
            "example_action_chain_failure",
            "example_messages_page",
            "example_message_single",
            "example_message_multiple",
            "example_message_trusted_html",
            "example_feedback_default",
            "example_feedback_target",
            "example_feedback_message",
            "example_feedback_no_duplicate",
            "example_feedback_error",
            "example_navigation_page",
            "example_navigation_slow_action",
            "example_navigation_loading_wait",
            "example_sortable_page",
            "example_sortable_move",
            "example_charts_page",
            "example_chart_data",
            "example_realtime_chart_events",
            "example_realtime_chart_publish",
            "example_notifications_page",
            "example_notification_send",
            "example_auth_probe",
            "example_auth_page",
            "example_session_page",
            "example_session_revoke",
            "example_session_expire",
            "example_i18n_page",
            "example_ui_page",
        ):
            self.assertIn(f"{self.app.name}.{name}", route_names)

        sidebar = (
            Environment(autoescape=True)
            .from_string(
                (ROOT / "templates/partials/sidebar.html").read_text(encoding="utf-8")
            )
            .render(_=lambda value: value, active_page="", active_section="")
        )
        for category, section in EXAMPLE_SECTIONS.items():
            pages = section["pages"]
            assert isinstance(pages, dict)
            for page in pages:
                path = (
                    f"/examples/{category}"
                    if category == "plugins"
                    else f"/examples/{category}/{page}"
                )
                self.assertIn(f'href="{path}"', sidebar)
        self.assertNotIn('href="/examples/plugins/index"', sidebar)

    def test_every_page_uses_staff_guard_shared_context_and_examples_entry(
        self,
    ) -> None:
        from apps.examples import views

        staff = DashboardSessionData(user_id=1, is_active=True, is_staff=True)
        rendered = object()
        with patch.object(
            views, "render_template", new=AsyncMock(return_value=rendered)
        ) as render:
            for category, section in views.EXAMPLE_SECTIONS.items():
                pages = section["pages"]
                assert isinstance(pages, dict)
                for page in pages:
                    path = f"/examples/{category}/{page}"
                    if category == "plugins":
                        response = asyncio.run(
                            views.examples_plugins(
                                request_with_session(staff, "/examples/plugins")
                            )
                        )
                    else:
                        response = asyncio.run(
                            views.examples_page(
                                request_with_session(staff, path), category, page
                            )
                        )
                    self.assertIs(rendered, response)
                    if category == "plugins":
                        self.assertEqual(
                            "pages/examples/plugins.html", render.await_args.args[0]
                        )
                    context = render.await_args.kwargs["context"]
                    self.assertEqual("examples", context["page_entry"])
                    self.assertEqual(
                        f"examples_{category.replace('-', '_')}",
                        context["active_section"],
                    )

    def test_forms_slice_uses_real_templates_and_delegates_shared_pages(self) -> None:
        """Implemented Form pages render directly or delegate to their owning module."""
        from apps.examples.views import forms as form_views
        from apps.examples.views import data_inputs
        from apps.examples.views import storage

        staff = DashboardSessionData(user_id=1, is_active=True, is_staff=True)
        rendered = object()
        handler = inspect.unwrap(form_views.example_forms_page)
        pages = (
            "basics",
            "choices",
            "layouts",
            "validation",
            "date-time",
            "masks",
            "sliders",
            "slug",
            "input-spinner",
            "tags",
            "color-picker",
            "rich-text",
            "multi-step",
            "json-list",
            "containers",
        )
        with (
            patch.object(
                form_views, "render_template", new=AsyncMock(return_value=rendered)
            ) as render,
            patch.object(
                form_views.services,
                "list_stream_profiles",
                new=AsyncMock(return_value=[]),
            ),
        ):
            for page in pages:
                response = asyncio.run(
                    handler(
                        request_with_session(staff, f"/examples/forms/{page}"), page
                    )
                )
                self.assertIs(rendered, response)
                self.assertTrue(
                    render.await_args.args[0].startswith("pages/examples/forms/")
                )

        with patch.object(
            data_inputs, "render_remote_form_page", new=AsyncMock(return_value=rendered)
        ) as concrete:
            response = asyncio.run(
                handler(
                    request_with_session(staff, "/examples/forms/selects"), "selects"
                )
            )
        self.assertIs(rendered, response)
        concrete.assert_awaited_once()

        with patch.object(
            storage, "render_upload_page", new=AsyncMock(return_value=rendered)
        ) as concrete:
            response = asyncio.run(
                handler(request_with_session(staff, "/examples/forms/upload"), "upload")
            )
        self.assertIs(rendered, response)
        concrete.assert_awaited_once()

    def test_slug_success_response_shows_the_server_normalized_value(self) -> None:
        """Both response modes must expose the final value produced by SlugField."""
        from apps.examples.forms import SlugExampleForm
        from apps.examples.views import forms as form_views

        form = SlugExampleForm(
            data={
                "title": "Server Final Title",
                "slug": "",
                "unicode_title": "中文 页面",
                "unicode_slug": "",
            }
        )
        self.assertTrue(asyncio.run(form.validate()))
        request = SimpleNamespace(
            app=self.app,
            headers={"accept": "application/json"},
        )

        response = asyncio.run(
            form_views._form_success_response(request, page="slug", form=form)
        )
        payload = json.loads(response.body)

        self.assertEqual(payload["actions"][1]["action"], "replace_html")
        self.assertIn('data-example-form-result="slug"', payload["actions"][1]["html"])
        self.assertIn("server-final-title", payload["actions"][1]["html"])

    def test_drag_and_button_moves_both_replace_the_authoritative_board(self) -> None:
        """A successful drag must refresh counts and the next button request too."""
        from contextlib import asynccontextmanager
        from apps.examples.views import sortable

        @asynccontextmanager
        async def transaction():
            yield object()

        handler = inspect.unwrap(sortable.example_sortable_move)
        for interaction in ("", "keyboard"):
            with (
                self.subTest(interaction=interaction),
                patch.object(sortable, "db_manager", SimpleNamespace(get_session=transaction)),
                patch.object(sortable.services, "move_sortable_task", new=AsyncMock()),
                patch.object(sortable, "_render_board", new=AsyncMock(return_value="<section>current board</section>")),
            ):
                request = SimpleNamespace(form={"item_id": "1", "source_list": "todo",
                                               "target_list": "review", "target_position": "0",
                                               "interaction": interaction}, args={})
                payload = json.loads(asyncio.run(handler(request)).body)
                self.assertEqual(0, payload["error_code"])
                self.assertEqual([{"action": "replace_html", "target": "#sortable-workflow-board",
                                   "html": "<section>current board</section>", "swap": "outer"}], payload["actions"])

    def test_tags_page_uses_both_form_modes_and_shows_normalized_values(self) -> None:
        """Tags 示例同时展示两种响应模式及服务端最终数据。"""
        from apps.examples.forms import TagsExampleForm
        from apps.examples.views import forms as form_views

        staff = DashboardSessionData(user_id=1, is_active=True, is_staff=True)
        rendered = object()
        handler = inspect.unwrap(form_views.example_forms_page)
        with patch.object(form_views, "render_template", new=AsyncMock(return_value=rendered)) as render:
            response = asyncio.run(handler(request_with_session(staff, "/examples/forms/tags"), "tags"))

        self.assertIs(response, rendered)
        cards = render.await_args.kwargs["context"]["form_cards"]
        self.assertEqual(["html", "json"], [card["mode"] for card in cards])
        self.assertEqual(",", cards[0]["form"].keywords.delimiter)
        self.assertEqual("|", cards[0]["form"].aliases.delimiter)
        self.assertIsNotNone(cards[0]["form"].select_secret_key)

        form = TagsExampleForm(
            data={
                "keywords": " Python, sanic,,Python ",
                "aliases": "api | web | api",
                "categories": ["backend", "dashboard"],
                "tag_ids": [1, 16],
            }
        )
        self.assertTrue(asyncio.run(form.validate()))
        request = SimpleNamespace(app=self.app, headers={"accept": "application/json"})
        result = asyncio.run(form_views._form_success_response(request, page="tags", form=form))
        payload = json.loads(result.body)
        html = payload["actions"][1]["html"]

        self.assertIn("Python,sanic", html)
        self.assertIn("api|web", html)
        self.assertIn("backend", html)
        self.assertIn("dashboard", html)
        self.assertIn("[1, 16]", html)

    def test_examples_reject_anonymous_and_non_staff_sessions(self) -> None:
        from apps.examples import views

        anonymous = asyncio.run(
            views.examples_page(
                request_with_session(DashboardSessionData(), "/examples/tables/static"),
                "tables",
                "static",
            )
        )
        non_staff = asyncio.run(
            views.examples_page(
                request_with_session(
                    DashboardSessionData(user_id=2, is_active=True, is_staff=False),
                    "/examples/tables/static",
                ),
                "tables",
                "static",
            )
        )

        self.assertEqual(302, anonymous.status)
        self.assertIn("/login?next=", anonymous.headers["location"])
        self.assertEqual(403, non_staff.status)

    def test_notification_example_uses_current_user_and_fixed_html(self) -> None:
        from apps.examples.views import notifications as notification_views
        from oldman.web.messages import MessageFormat, MessageLevel
        from oldman.web.messages.notifications import NotificationPresentation

        request = request_with_session(
            DashboardSessionData(user_id=17, is_active=True, is_staff=True),
            "/examples/notifications/send",
        )
        request.form = {
            "mode": "persistent",
            "level": "warning",
            "presentation": "modal",
            "title": "Browser title",
            "body": "<script>ignored</script>",
            "content_mode": "trusted_html",
            "href": "/user-notifications",
            "icon": "ri-notification-3-line",
        }
        create = AsyncMock(return_value=SimpleNamespace(id=23))
        handler = inspect.unwrap(notification_views.example_notification_send)

        with patch.object(notification_views.notifications, "create", new=create):
            response = asyncio.run(handler(request))

        self.assertEqual(response.status, 200)
        arguments = create.await_args.kwargs
        self.assertEqual(arguments["user_id"], 17)
        self.assertEqual(arguments["level"], MessageLevel.WARNING)
        self.assertEqual(arguments["presentation"], NotificationPresentation.MODAL)
        self.assertEqual(arguments["format"], MessageFormat.HTML)
        self.assertNotIn("ignored", arguments["body"])
        self.assertIn("data-example-notification-html", arguments["body"])

    def test_notification_example_rejects_temporary_center_only_delivery(
        self,
    ) -> None:
        from apps.examples.views import notifications as notification_views

        request = request_with_session(
            DashboardSessionData(user_id=31, is_active=True, is_staff=True),
            "/examples/notifications/send",
        )
        request.form = {
            "mode": "temporary",
            "level": "info",
            "presentation": "none",
            "title": "Invalid push",
            "body": "",
            "content_mode": "text",
            "href": "",
            "icon": "",
        }
        push = AsyncMock(
            side_effect=ValueError("temporary push presentation must not be none")
        )
        handler = inspect.unwrap(notification_views.example_notification_send)

        with patch.object(notification_views.notifications, "push", new=push):
            response = asyncio.run(handler(request))

        self.assertEqual(response.status, 200)
        self.assertIn(b'"error_code":1000', response.body)
        push.assert_awaited_once()

    def test_auth_example_projects_the_current_session_without_a_user_query(
        self,
    ) -> None:
        from apps.examples.views import auth_session_i18n as example_views

        request = request_with_session(
            DashboardSessionData(
                user_id=41,
                username="current-staff",
                display_name="Current Staff",
                is_active=True,
                is_staff=True,
            ),
            "/examples/auth/identity",
        )
        rendered = object()
        handler = inspect.unwrap(example_views.example_auth_page)
        with patch.object(
            example_views, "render_template", new=AsyncMock(return_value=rendered)
        ) as render:
            response = asyncio.run(handler(request, "identity"))

        self.assertIs(response, rendered)
        context = render.await_args.kwargs["context"]
        self.assertEqual(context["session"].user_id, 41)
        self.assertEqual(context["session_profile"].display_name, "Current Staff")

    def test_session_examples_use_the_installed_manager_for_expiry_and_revoke(
        self,
    ) -> None:
        from apps.examples.views import auth_session_i18n as example_views

        request = request_with_session(
            DashboardSessionData(user_id=52, is_active=True, is_staff=True),
            "/examples/session/revoke",
        )
        manager = request.app.ctx.session
        revoke_sessions = AsyncMock(return_value=("one", "two"))
        logout = AsyncMock()
        with (
            patch.object(manager, "force_logout_user", new=revoke_sessions),
            patch.object(manager, "get_session_id", return_value="current-sid"),
            patch.object(manager, "logout", new=logout),
        ):
            revoke = asyncio.run(
                inspect.unwrap(example_views.example_session_revoke)(request)
            )
            expire = asyncio.run(
                inspect.unwrap(example_views.example_session_expire)(request)
            )

        self.assertEqual(revoke.status, 200)
        self.assertEqual(expire.status, 200)
        revoke_sessions.assert_awaited_once_with(52)
        logout.assert_awaited_once_with("current-sid")

    def test_auth_probe_rejects_anonymous_and_non_staff_sessions(self) -> None:
        from apps.examples.views import auth_session_i18n as example_views

        anonymous = request_with_session(DashboardSessionData(), "/examples/auth/probe")
        non_staff = request_with_session(
            DashboardSessionData(user_id=7, is_active=True, is_staff=False),
            "/examples/auth/probe",
        )
        anonymous.headers = {"accept": "application/json"}
        non_staff.headers = {"accept": "application/json"}

        anonymous_response = asyncio.run(example_views.example_auth_probe(anonymous))
        non_staff_response = asyncio.run(example_views.example_auth_probe(non_staff))

        self.assertEqual(anonymous_response.status, 401)
        self.assertEqual(non_staff_response.status, 403)

    def test_i18n_examples_use_the_owned_templates(self) -> None:
        from apps.examples.views import auth_session_i18n as example_views

        request = request_with_session(
            DashboardSessionData(user_id=9, is_active=True, is_staff=True),
            "/examples/i18n/browser",
        )
        rendered = object()
        handler = inspect.unwrap(example_views.example_i18n_page)
        with patch.object(
            example_views, "render_template", new=AsyncMock(return_value=rendered)
        ) as render:
            response = asyncio.run(handler(request, "browser"))

        self.assertIs(response, rendered)
        render.assert_awaited_once_with(
            "pages/examples/i18n/browser.html",
            context=example_views._page_context("i18n", "browser"),
        )

    def test_ui_reference_pages_use_concrete_templates(self) -> None:
        from apps.examples.views import ui as ui_views

        request = request_with_session(
            DashboardSessionData(user_id=9, is_active=True, is_staff=True),
            "/examples/ui/icons",
        )
        rendered = object()
        handler = inspect.unwrap(ui_views.example_ui_page)
        with patch.object(
            ui_views, "render_template", new=AsyncMock(return_value=rendered)
        ) as render:
            for page in ui_views.OWNED_UI_PAGES:
                response = asyncio.run(handler(request, page))
                self.assertIs(response, rendered)
                self.assertEqual(
                    f"pages/examples/ui/{page}.html", render.await_args.args[0]
                )
                self.assertEqual(
                    f"examples_ui_{page.replace('-', '_')}",
                    render.await_args.kwargs["context"]["active_page"],
                )


if __name__ == "__main__":
    unittest.main()
