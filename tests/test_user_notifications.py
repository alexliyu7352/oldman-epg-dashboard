"""End-to-end assembly test for the EPG user-notification host integration."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


class EpgUserNotificationsTest(unittest.TestCase):
    """Build the real Dashboard app in isolation and inspect its public contracts."""

    def test_dashboard_registers_shared_notification_routes_and_dom(self) -> None:
        script = r'''
import asyncio
import json
from types import SimpleNamespace

from oldman import bootstrap_service
import sys

bootstrap_service("web", config_file=sys.argv[1])

from apps.auth.session import DashboardSessionData
from config.settings import settings
from services.web import WebService

app = WebService(settings.core.app_name).create_app()
from apps.auth import views as auth_views
environment = app.ext.environment
session = DashboardSessionData(
    user_id=7,
    username="staff",
    display_name="Dashboard Staff",
    is_active=True,
    is_staff=True,
    is_superuser=False,
)
request = SimpleNamespace(
    app=app,
    args={},
    cookies={},
    ctx=SimpleNamespace(session=session, locale="en"),
    headers={},
    method="GET",
    path="/dashboard",
    query_string="",
)
denied_request = SimpleNamespace(
    app=app,
    args={},
    cookies={},
    ctx=SimpleNamespace(session=DashboardSessionData(
        user_id=8,
        username="ordinary",
        is_active=True,
        is_staff=False,
        is_superuser=False,
    )),
    headers={},
    method="GET",
    path="/user-notifications",
    query_string="",
)

async def render_templates():
    activity = [SimpleNamespace(
        tone="warning",
        icon="ri-alert-line",
        href="/notifications",
        title="Activity",
        description="Existing EPG activity",
        time="Now",
    )]
    topbar = await environment.get_template("partials/topbar.html").render_async(
        request=request,
        dashboard_notifications=activity,
    )
    base = await environment.get_template("base.html").render_async(
        request=request,
        messages=(),
        topbar_dashboard_notifications=lambda: activity,
    )
    center = await environment.get_template("pages/user_notifications.html").render_async(
        request=request,
        messages=(),
        notification_center_content="<section data-shared-center></section>",
        topbar_dashboard_notifications=lambda: activity,
    )
    return topbar, base, center

topbar, base, center = asyncio.run(render_templates())
denied_center = asyncio.run(auth_views.user_notifications(denied_request))
denied_events = asyncio.run(auth_views.user_events(denied_request))
routes = sorted(route.uri for route in app.router.routes)
print(json.dumps({
    "activity_route_present": "/notifications" in routes,
    "base_has_user_events_meta": 'name="oldman-user-events-url" content="/user-events"' in base,
    "center_uses_shared_content": "data-shared-center" in center,
    "center_requires_staff": denied_center.status == 403,
    "persistent_center_route_present": "/user-notifications" in routes,
    "persistent_delete_absent_from_topbar": "data-om-user-notification-delete-selected" not in topbar,
    "persistent_select_absent_from_topbar": "data-om-user-notification-select" not in topbar,
    "persistent_topbar_present": "data-om-user-notification-topbar" in topbar,
    "runtime_activity_present": "data-om-activity-notification-item" in topbar,
    "user_events_route_present": "/user-events" in routes,
    "user_events_requires_staff": denied_events.status == 403,
    "user_notification_urls": environment.globals.get("dashboard_user_notification_urls"),
}))
'''
        environment = os.environ.copy()
        python_path = [str(EXAMPLE_ROOT)]
        if environment.get("PYTHONPATH"):
            python_path.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(python_path)
        yaml = YAML()
        config = yaml.load(
            (EXAMPLE_ROOT / "data" / "web_settings.example.yaml").read_text(
                encoding="utf-8"
            )
        )
        config["web"]["security"]["secret_key"] = (
            "epg-notification-test-secret-key-2026"
        )
        config["web"]["security"]["fingerprint"]["aes_secret_key"] = (
            base64.urlsafe_b64encode(b"e" * 32).decode("ascii")
        )
        with tempfile.NamedTemporaryFile(
            dir=EXAMPLE_ROOT / "data",
            mode="w",
            suffix=".yaml",
            encoding="utf-8",
        ) as config_file:
            yaml.dump(config, config_file)
            config_file.flush()
            result = subprocess.run(
                [sys.executable, "-c", script, config_file.name],
                cwd=EXAMPLE_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])

        self.assertTrue(payload["user_events_route_present"], payload)
        self.assertTrue(payload["persistent_center_route_present"])
        self.assertTrue(payload["activity_route_present"])
        self.assertTrue(payload["base_has_user_events_meta"])
        self.assertTrue(payload["center_uses_shared_content"])
        self.assertTrue(payload["center_requires_staff"])
        self.assertTrue(payload["persistent_topbar_present"])
        self.assertTrue(payload["runtime_activity_present"])
        self.assertTrue(payload["persistent_select_absent_from_topbar"])
        self.assertTrue(payload["persistent_delete_absent_from_topbar"])
        self.assertTrue(payload["user_events_requires_staff"])
        self.assertEqual(
            payload["user_notification_urls"],
            {
                "center": "/user-notifications",
                "topbar": "/user-notifications/topbar",
            },
        )


if __name__ == "__main__":
    unittest.main()
