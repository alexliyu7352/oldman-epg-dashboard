"""Register receiving declarations through the normal App Registry."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _


class CommunicationAppConfig(AppConfig):
    """No models or settings: reuse Examples data and the global bus configuration."""

    label = "communication"
    display_name = _("Service communication")
    icon = "ri-share-line"


app = CommunicationAppConfig()
