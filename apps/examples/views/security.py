"""Browser fingerprint demo: one guarded endpoint, called four ways.

The page drives the same probe route four times - with a valid payload, without the
header, with one byte flipped, and repeatedly until the limiter refuses - so every
outcome comes from the real guard rather than a simulated one.
"""

from __future__ import annotations

from oldman.conf import settings
from oldman.i18n import gettext_lazy as _
from oldman.web import router
from oldman.web.auth import staff_required
from oldman.web.request import Request
from oldman.web.response import json_response
from oldman.web.security import fingerprint_required
from oldman.web.template import render_template

from . import EXAMPLE_SECTIONS

#: Its own rate-limit bucket, tuned low so the 429 card is reachable by clicking.
PROBE_ENDPOINT = "/examples/security/fingerprint/probe"


@router.get("/examples/security/fingerprint", name="example_security_fingerprint_page")
@staff_required()
async def example_security_fingerprint_page(request: Request):
    """Render the four probes and the notes on what this does and does not prove."""
    section = EXAMPLE_SECTIONS["security"]
    pages = section["pages"]
    assert isinstance(pages, dict)
    fingerprint = settings.web.security.fingerprint
    return await render_template(
        "pages/examples/security/fingerprint.html",
        context={
            "active_page": "examples_security_fingerprint",
            "active_section": "examples_security",
            "example_category": "security",
            "example_page": "fingerprint",
            "example_page_title": pages["fingerprint"],
            "example_section": section,
            "page_entry": "examples",
            "probe_endpoint": PROBE_ENDPOINT,
            "rate_limit": fingerprint.rate_limits.get(PROBE_ENDPOINT, fingerprint.rate_limits["default"]),
            "timestamp_max_diff_seconds": fingerprint.timestamp_max_diff // 1000,
        },
    )


@router.get(PROBE_ENDPOINT, name="example_security_fingerprint_probe")
@staff_required()
@fingerprint_required(endpoint=PROBE_ENDPOINT)
async def example_security_fingerprint_probe(request: Request):
    """Reached only by a request whose fingerprint decrypted, was fresh, and passed the limiter."""
    decision = request.ctx.fingerprint_decision
    return json_response(
        {
            "error_code": 0,
            "message": str(_("Fingerprint accepted")),
            "data": {
                "visitor_id": request.ctx.visitor_id,
                "fingerprint_count": decision.fingerprint_count,
                "ip_count": decision.ip_count,
                "anomalies": decision.anomalies,
            },
        }
    )
