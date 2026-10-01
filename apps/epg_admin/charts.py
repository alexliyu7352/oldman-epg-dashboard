"""Captions shared by the EPG charts on the dashboard and the catalog pages."""

from __future__ import annotations

from oldman.i18n import gettext as _
from oldman.web.components.charts import ChartRequest


def chart_caption(chart_request: ChartRequest, metric: str) -> dict[str, object]:
    """The chart's `meta`, shown under the plot as it is: the selected range and the metric, translated.

    Keys and values are what the reader sees, so neither is an identifier such as `range` or `30d`.
    """
    # The catalog pages chart every record (`all`); the dashboard's ranges are `<days>d`.
    selected = _("All") if chart_request.range_key == "all" else f"{chart_request.range_days()} {_('Days')}"
    return {_("Range"): selected, _("Metric"): metric}
