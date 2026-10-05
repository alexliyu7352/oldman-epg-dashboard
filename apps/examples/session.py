"""Strongly typed Session data used by the EPG Dashboard."""

from __future__ import annotations

from oldman.web.request import Request
from oldman.web.session import SessionData, get_session_data


class DashboardSessionData(SessionData, kw_only=True):
    """Keep an application-specific Session type without duplicating base fields."""


def dashboard_session(request: Request) -> DashboardSessionData:
    """Return this application's concrete request Session model."""
    return get_session_data(request, DashboardSessionData)


__all__ = ["DashboardSessionData", "dashboard_session"]
