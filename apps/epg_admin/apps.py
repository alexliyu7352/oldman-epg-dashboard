"""EPG management application metadata."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _


class EpgAdminAppConfig(AppConfig):
    """Own the EPG schema and management routes."""

    label = "epg_admin"
    display_name = _("EPG Management")
    icon = "ri-tv-2-line"


app = EpgAdminAppConfig()

__all__ = ["EpgAdminAppConfig", "app"]
