"""Dashboard overview application metadata."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _


class DashboardAppConfig(AppConfig):
    """Describe the Dashboard overview routes."""

    label = "dashboard"
    display_name = _("Dashboard")
    icon = "ri-dashboard-2-line"


app = DashboardAppConfig()

__all__ = ["DashboardAppConfig", "app"]
