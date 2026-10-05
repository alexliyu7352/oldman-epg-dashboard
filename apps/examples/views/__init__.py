"""Dashboard example shell routes."""

from __future__ import annotations

from oldman.i18n import gettext_lazy as _
from oldman.web import NotFound, router
from oldman.web.auth import login_required
from oldman.web.request import Request
from oldman.web.response import redirect_response
from oldman.web.template import render_template

EXAMPLE_SECTIONS: dict[str, dict[str, object]] = {
    "communication": {
        "title": _("Service communication"),
        "pages": {"rpc": _("Immediate RPC"), "events": _("Events and Broadcast"), "failures": _("Communication Failures")},
    },
    "tasks": {
        "title": _("Distributed Tasks"),
        "pages": {"results": _("Submission and Results"), "schedules": _("Schedules and Retry"), "queues": _("Queues and Broadcast")},
    },
    "http": {
        "title": _("HTTP"),
        "pages": {"client": _("Backend HTTP Client")},
    },
    "cache": {
        "title": _("Cache"),
        "pages": {"redis": _("Redis Cache"), "models": _("Model Cache")},
    },
    "config": {
        "title": _("Dynamic Configuration"),
        "pages": {"file": _("File Configuration"), "redis": _("Redis Configuration")},
    },
    "security": {
        "title": _("Security"),
        "pages": {"fingerprint": _("Browser Fingerprint")},
    },
    "tables": {
        "title": _("Tables"),
        "pages": {
            "static": _("Static HTML"),
            "responsive": _("Responsive Table"),
            "html": _("HTML Data Table"),
            "json": _("JSON Data Table"),
            "states": _("Table States"),
            "realtime": _("Realtime Table"),
            "advanced": _("Advanced Tables"),
        },
    },
    "forms": {
        "title": _("Forms"),
        "pages": {
            "basics": _("Basic Fields"),
            "choices": _("Choice Fields"),
            "layouts": _("Form Layouts"),
            "booleans": _("Boolean Widgets"),
            "validation": _("Validation"),
            "selects": _("Selects"),
            "autocomplete": _("Autocomplete"),
            "date-time": _("Date and Time"),
            "masks": _("Input Masks"),
            "sliders": _("Sliders"),
            "slug": _("Slug"),
            "input-spinner": _("Input Spinner"),
            "tags": _("Tags"),
            "color-picker": _("Color Picker"),
            "rich-text": _("Rich Text Editor"),
            "multi-step": _("Multi-step Form"),
            "upload": _("File Upload"),
            "json-list": _("JSON Lists"),
            "containers": _("Form Containers"),
        },
    },
    "modals": {
        "title": _("Modals"),
        "pages": {
            "basics": _("Modal Basics"),
            "remote": _("Remote Content"),
            "workflows": _("Modal Workflows"),
            "actions": _("Response Actions"),
        },
    },
    "messages": {
        "title": _("Messages"),
        "pages": {"page": _("Page Messages"), "feedback": _("Feedback")},
    },
    "notifications": {
        "title": _("Notifications"),
        "pages": {
            "generator": _("Notification Generator"),
            "center": _("Notification Center"),
            "realtime": _("Realtime Notifications"),
        },
    },
    "storage": {
        "title": _("Storage"),
        "pages": {
            "upload": _("Upload"),
            "lifecycle": _("File Lifecycle"),
            "api": _("Storage API"),
        },
    },
    "data-inputs": {
        "title": _("Data Inputs"),
        "pages": {
            "lists": _("Editable Lists"),
            "autocomplete": _("Remote Autocomplete"),
            "providers": _("Data Providers"),
        },
    },
    "sortable": {
        "title": _("Sortable"),
        "pages": {"workflow": _("Review Workflow")},
    },
    "charts": {
        "title": _("Charts"),
        "pages": {
            "trends": _("Trends"),
            "composition": _("Composition"),
            "distribution": _("Distribution"),
            "states": _("Chart States"),
            "realtime": _("Realtime Charts"),
        },
    },
    "navigation": {
        "title": _("Navigation"),
        "pages": {
            "lifecycle": _("Page Lifecycle"),
            "actions": _("Navigation Actions"),
            "loading": _("Loading States"),
        },
    },
    "auth": {
        "title": _("Authentication"),
        "pages": {
            "login": _("Login"),
            "guards": _("Access Guards"),
            "identity": _("Current Identity"),
            "tokens": _("Access Tokens"),
            "callers": _("Service Callers"),
        },
    },
    "session": {
        "title": _("Session"),
        "pages": {
            "lifecycle": _("Session Lifecycle"),
            "revoke": _("Session Revoke"),
            "expiry": _("Session Expiry"),
        },
    },
    "i18n": {
        "title": _("Internationalization"),
        "pages": {
            "server": _("Server Translation"),
            "browser": _("Browser Translation"),
            "coverage": _("Translation Coverage"),
        },
    },
    "ui": {
        "title": _("UI Reference"),
        "pages": {
            "foundations": _("Foundations"),
            "typography": _("Typography"),
            "buttons": _("Buttons"),
            "badges-avatars": _("Badges and Avatars"),
            "cards": _("Cards"),
            "lists": _("Lists"),
            "alerts": _("Alerts"),
            "progress-loading": _("Progress and Loading"),
            "carousel": _("Horizontal Browsing"),
            "timeline": _("Timeline"),
            "navigation-shell": _("Navigation Shell"),
            "tabs-disclosure": _("Tabs and Disclosure"),
            "dropdowns-overlays": _("Dropdowns and Overlays"),
            "countdown": _("Countdown"),
            "gallery": _("Gallery"),
            "video": _("Video"),
            "system-states": _("System States"),
            "images": _("Images"),
            "icons": _("Icons"),
        },
    },
    "plugins": {
        "title": _("Plugins"),
        "pages": {"index": _("Plugin Index")},
    },
}



@router.get("/examples", name="examples_index")
@login_required()
async def examples_index(request: Request):
    """Open the first concrete example page."""
    return redirect_response("/examples/tables/static")


@router.get("/examples/plugins", name="examples_plugins")
@login_required()
async def examples_plugins(request: Request):
    """Render the plugin capability index."""
    section = EXAMPLE_SECTIONS["plugins"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    return await render_template(
        "pages/examples/plugins.html",
        context={
            "active_page": "examples_plugins_index",
            "active_section": "examples_plugins",
            "example_category": "plugins",
            "example_page": "index",
            "example_page_title": pages["index"],
            "example_section": section,
            "page_entry": "examples",
        },
    )


@router.get("/examples/<category:str>/<page:str>", name="examples_page")
@login_required()
async def examples_page(request: Request, category: str, page: str):
    """Render a documented example route or return a real 404."""
    return await _render_example(request, category, page)


async def _render_example(request: Request, category: str, page: str):
    """Validate the route and build the shared example page context."""
    section = EXAMPLE_SECTIONS.get(category)
    pages = section.get("pages") if section else None
    if not isinstance(pages, dict) or page not in pages:
        raise NotFound("Example page was not found")

    return await render_template(
        "pages/examples/base.html",
        context={
            "active_page": f"examples_{category.replace('-', '_')}_{page.replace('-', '_')}",
            "active_section": f"examples_{category.replace('-', '_')}",
            "example_category": category,
            "example_page": page,
            "example_page_title": pages[page],
            "example_section": section,
            "page_entry": "examples",
        },
    )


__all__ = ["EXAMPLE_SECTIONS"]

# Import concrete route modules only after the shared section inventory exists.
from . import auth_session_i18n as _auth_session_i18n  # noqa: E402,F401
from . import cache as _cache  # noqa: E402,F401
from . import charts as _charts  # noqa: E402,F401
from . import communication as _communication  # noqa: E402,F401
from . import config as _config  # noqa: E402,F401
from . import data_inputs as _data_inputs  # noqa: E402,F401
from . import forms as _forms  # noqa: E402,F401
from . import http as _http  # noqa: E402,F401
from . import messages as _messages  # noqa: E402,F401
from . import modals as _modals  # noqa: E402,F401
from . import navigation as _navigation  # noqa: E402,F401
from . import notifications as _notifications  # noqa: E402,F401
from . import security as _security  # noqa: E402,F401
from . import sortable as _sortable  # noqa: E402,F401
from . import storage as _storage  # noqa: E402,F401
from . import tables as _tables  # noqa: E402,F401
from . import tasks as _tasks  # noqa: E402,F401
from . import ui as _ui  # noqa: E402,F401
