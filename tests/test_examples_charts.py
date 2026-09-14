"""Chart example contracts that do not require a live browser."""

from __future__ import annotations

import datetime as dt
import unittest
from decimal import Decimal
from types import SimpleNamespace

from apps.examples.chart_views import (
    CHART_KEYS,
    COMPOSITION_CHARTS,
    DISTRIBUTION_CHARTS,
    REALTIME_CHART,
    TREND_CHARTS,
    ExampleChartData,
)
from apps.examples.services import realtime_chart_payload, realtime_chart_stream


class ExampleChartTests(unittest.TestCase):
    def test_every_documented_chart_family_has_a_data_key(self) -> None:
        self.assertTrue(
            set(
                (
                    *TREND_CHARTS,
                    *COMPOSITION_CHARTS,
                    *DISTRIBUTION_CHARTS,
                    REALTIME_CHART,
                )
            )
            <= CHART_KEYS
        )

    def test_realtime_payload_is_typed_and_targets_one_stable_stream(self) -> None:
        metric = SimpleNamespace(
            server_id=7,
            sampled_at=dt.datetime(2026, 9, 2, 10, 30),
            cpu_percent=Decimal("41.25"),
            memory_percent=Decimal("63.50"),
            upload_mbps=Decimal("120.75"),
            download_mbps=Decimal("240.25"),
        )

        payload = realtime_chart_payload(metric, source="stream")

        self.assertEqual(payload.server_id, 7)
        self.assertEqual(payload.cpu_percent, 41.25)
        self.assertEqual(payload.source, "stream")
        self.assertEqual(realtime_chart_stream(7), "examples.monitoring.server-7")


class ExampleRealtimeChartTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_series_are_empty_before_sse_replays_database_rows(self) -> None:
        chart = ExampleChartData(request=SimpleNamespace(ctx=SimpleNamespace()))

        result = await chart._server_realtime(
            SimpleNamespace(filters={"server_id": "7"})
        )

        self.assertEqual([], result.series[0].data)
        self.assertEqual([], result.series[1].data)
        self.assertEqual([], result.labels)
        self.assertEqual([], result.summary)


class ExampleChartI18nTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_result_uses_the_request_translation_catalog(self) -> None:
        request = SimpleNamespace(
            ctx=SimpleNamespace(translations=PrefixCatalog()),
        )
        chart = ExampleChartData(request=request)
        chart._chart_key = "project-completion"
        chart.db_session = SimpleNamespace(scalar=async_value(42.5))

        result = await chart.get_result(
            SimpleNamespace(filters={}, range_key="30d", request=request)
        )
        payload = result.to_apex_options()

        self.assertEqual(["译文:Average completion"], payload["labels"])
        self.assertEqual(
            "译文:Projects",
            payload["plotOptions"]["radialBar"]["dataLabels"]["total"]["label"],
        )
        self.assertEqual("译文:Example database", payload["meta"]["source"])


class PrefixCatalog:
    def gettext(self, message: str) -> str:
        return f"译文:{message}"

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        return f"译文:{singular if n == 1 else plural}"

    def pgettext(self, _context: str, message: str) -> str:
        return f"译文:{message}"


def async_value(value):
    async def resolve(*_args, **_kwargs):
        return value

    return resolve


if __name__ == "__main__":
    unittest.main()
