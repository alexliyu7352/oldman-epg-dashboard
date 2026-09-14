"""Dashboard examples application metadata."""

from oldman.apps import AppConfig
from oldman.i18n import gettext_lazy as _

from .settings import ExamplesSettings


class ExamplesAppConfig(AppConfig[ExamplesSettings]):
    """Own the Dashboard's reusable example data and views."""

    label = "examples"
    display_name = _("Dashboard Examples")
    icon = "ri-flask-line"
    settings_model = ExamplesSettings


app = ExamplesAppConfig()

__all__ = ["ExamplesAppConfig", "app"]
