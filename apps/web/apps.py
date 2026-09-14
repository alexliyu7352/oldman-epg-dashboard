"""Dashboard Web shell application metadata."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _


class DashboardWebAppConfig(AppConfig):
    """Describe Web shell routes owned by the project."""

    label = "epg_web"
    display_name = _("Web")
    icon = "ri-global-line"


app = DashboardWebAppConfig()

__all__ = ["DashboardWebAppConfig", "app"]
