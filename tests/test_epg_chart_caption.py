"""The caption under EPG charts: readable for every range the charts allow."""

from __future__ import annotations

import unittest

from oldman.web.components.charts import ChartRequest

from apps.epg_admin.charts import chart_caption


def _request(range_key: str) -> ChartRequest:
    return ChartRequest(request=None, range_key=range_key, group_by="", chart_type="bar", metric="m", filters={}, route_kwargs={})


class ChartCaptionTest(unittest.TestCase):
    def test_every_allowed_range_has_a_caption(self) -> None:
        """The catalog charts use `all`; parsing it as days made every one of them answer 400."""
        self.assertEqual({"Range": "All", "Metric": "Logo Assets"}, chart_caption(_request("all"), "Logo Assets"))
        self.assertEqual({"Range": "30 Days", "Metric": "Programmes"}, chart_caption(_request("30d"), "Programmes"))


if __name__ == "__main__":
    unittest.main()
