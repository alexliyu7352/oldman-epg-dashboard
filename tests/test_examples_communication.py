"""Focused Form/view contracts; real transport is checked by test_examples_nats."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import AsyncMock, patch

from nats.errors import NoRespondersError

from apps.examples.forms import CommunicationProjectForm
from apps.examples.nats_messages import ObservationReply, ProjectStatusReply
from oldman.conf import settings
from oldman.web import NotFound


class CommunicationViewTest(unittest.IsolatedAsyncioTestCase):
    """Use actual field validation and rendered response actions, not string checks."""

    @classmethod
    def setUpClass(cls):
        """Use the existing isolated-bootstrap Web test factory without starting sockets."""
        from tests.test_web_app import create_test_app

        cls.app = create_test_app()
        from apps.examples.views import communication

        cls.views = communication

    def request(self, form=None):
        """The undecorated view receives the same mapping expected by SanicFormData."""
        return SimpleNamespace(app=self.app, method="POST", form=form or {}, files={}, ctx=SimpleNamespace(csrf_token="test-token"))

    async def test_choices_reject_empty_removed_and_arbitrary_peers(self):
        """Empty selection, removed records and arbitrary destination names stay invalid."""
        for project_id, peer_id, valid in (("1", "monitor_b", True), ("0", "monitor_a", False),
                                          ("999", "monitor_a", False), ("1", "someone_else", False)):
            form = CommunicationProjectForm(data={"query-project_id": project_id, "query-peer_id": peer_id}, prefix="query")
            form.project_id.choices = [(0, "Choose"), (1, "Real choice")]
            self.assertEqual(await form.validate(), valid)
            if not valid:
                self.assertTrue(form.to_api_response().errors)

    async def test_get_does_not_contact_receivers_even_when_disabled(self):
        """GET only builds controls and reads database choices, never messages."""
        render = AsyncMock(side_effect=lambda template, *, context: context)
        with (patch.object(self.views, "render_template", render),
              patch.object(self.views, "project_choices", AsyncMock(return_value=[])),
              patch.object(self.views.bus, "request", AsyncMock()) as request,
              patch.object(self.views.bus, "publish", AsyncMock()) as publish):
            for enabled in (False, True):
                with patch.object(settings.nats_bus, "enabled", enabled):
                    for page in ("rpc", "events", "failures"):
                        context = await inspect.unwrap(cast(Callable, self.views.example_communication_page))(self.request(), page)
                        self.assertEqual(context["bus_enabled"], enabled)
                        self.assertEqual(context["projects"], [])
            request.assert_not_awaited()
            publish.assert_not_awaited()

    async def test_partial_results_missing_record_and_real_errors(self):
        """Known network failures preserve good rows; database/programming errors propagate."""
        handler = inspect.unwrap(cast(Callable, self.views.example_communication_run))
        snapshot = ObservationReply(peer_id="monitor_a", pid=123, compete=2, broadcast=10, reports=1,
                                    compete_sender="web", broadcast_sender="web", report_sender="web")
        with patch.object(settings.nats_bus, "enabled", True):
            with patch.object(self.views, "query_observations", AsyncMock(side_effect=[snapshot, NoRespondersError()])):
                response = await handler(self.request(), "observe")
                payload = json.loads(response.body)
                self.assertEqual(response.status, 200)
                self.assertNotEqual(payload["error_code"], 0)
                self.assertIn('data-counter-peer="monitor_a"', payload["actions"][0]["html"])
                self.assertIn('data-counter-peer="monitor_b"', payload["actions"][0]["html"])
                self.assertIn("123", payload["actions"][0]["html"])
            with (patch.object(self.views, "project_choices", AsyncMock(return_value=[(1, "Project")])),
                  patch.object(self.views, "query_project_status", AsyncMock(return_value=ProjectStatusReply(
                      project_id=1, found=False, peer_id="monitor_a", pid=123)))):
                response = await handler(self.request({"query-project_id": "1", "query-peer_id": "monitor_a"}), "query")
                self.assertNotEqual(json.loads(response.body)["error_code"], 0)
            with patch.object(self.views, "project_choices", AsyncMock(side_effect=RuntimeError("database error"))):
                with self.assertRaisesRegex(RuntimeError, "database error"):
                    await handler(self.request(), "query")
            with self.assertRaises(NotFound):
                await handler(self.request(), "arbitrary.subject")
        with (patch.object(settings.nats_bus, "enabled", False),
              patch.object(self.views.bus, "request", AsyncMock()) as request):
            self.assertNotEqual(json.loads((await handler(self.request(), "query")).body)["error_code"], 0)
            request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
