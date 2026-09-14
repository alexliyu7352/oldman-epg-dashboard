"""Dashboard User application metadata."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _


class DashboardAuthAppConfig(AppConfig):
    """Own the Dashboard's concrete User model and authentication views."""

    label = "epg_auth"
    display_name = _("Users")
    icon = "ri-user-settings-line"


app = DashboardAuthAppConfig()

__all__ = ["DashboardAuthAppConfig", "app"]
