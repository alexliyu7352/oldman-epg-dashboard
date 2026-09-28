"""Walk the dynamic configuration examples with a temporary data directory and an owned Redis."""

from __future__ import annotations

import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oldman.conf as conf
from oldman.conf.schemas import DefaultSettings, RedisConfig
from oldman.providers.redis import RedisClientRegistry

from apps.examples import dynamic_config as example

ROOT = Path(__file__).resolve().parents[1]


def service_settings(data_dir: Path) -> DefaultSettings:
    settings = DefaultSettings()
    settings.core.namespace = "dynamic_config_example_test"
    settings.core.data_dir = data_dir
    return settings


class FileConfigExampleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="oldman-dynamic-config-test-")
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(conf.__dict__, {"settings": service_settings(Path(directory.name))}))
        example.channel_overrides.cache_clear()
        self.addCleanup(example.channel_overrides.cache_clear)
        # The page checks every two seconds; the test looks at the file on every read.
        example.channel_overrides().check_interval = 0

    async def test_own_writes_skip_the_hook_and_outside_edits_reach_it(self) -> None:
        first = await example.file_state()
        self.assertEqual({"cctv1", "tvb-jade"}, set(first.value.channels))
        self.assertIn("tvb-jade", first.file_text)

        await example.set_channel_offset("cctv1", 15)
        await example.set_announcement("Maintenance tonight")
        saved = await example.file_state()
        self.assertEqual(15, saved.value.channels["cctv1"].epg_offset_minutes)
        self.assertEqual("Maintenance tonight", saved.value.announcement)
        self.assertEqual(0, saved.outside_changes)

        await example.edit_announcement_outside("Edited in the file")
        edited = await example.file_state()
        self.assertEqual("Edited in the file", edited.value.announcement)
        self.assertEqual(15, edited.value.channels["cctv1"].epg_offset_minutes, "the outside edit keeps the other fields")
        self.assertEqual(1, edited.outside_changes)

        await example.break_file_outside()
        with self.assertLogs("default", level="ERROR"):
            broken = await example.file_state()
        self.assertIn("soon", broken.file_text)
        self.assertEqual(edited.value, broken.value, "the last valid value is still served")
        self.assertEqual(1, broken.outside_changes)

        await example.restore_file()
        restored = await example.file_state()
        self.assertEqual(example.ChannelOverrides(), restored.value)
        self.assertNotIn("soon", restored.file_text)


class RedisConfigExampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_values_and_devices_round_trip_through_redis(self) -> None:
        support = runpy.run_path(str(ROOT / "scripts/verify-dashboard-browser-with-server.py"))
        with tempfile.TemporaryDirectory(prefix="oldman-dynamic-config-test-") as temporary:
            root = Path(temporary)
            with support["owned_redis_server"](root / "redis", environment=os.environ) as url:
                registry = RedisClientRegistry(RedisConfig.model_validate({example.REDIS_ALIAS: {"redis_url": url}}))
                example.site_flags.cache_clear()
                example.allowed_devices.cache_clear()
                try:
                    with (
                        patch.dict(conf.__dict__, {"settings": service_settings(root)}),
                        patch.object(example, "redis_client", registry),
                    ):
                        await self.walk_the_page()
                finally:
                    example.site_flags.cache_clear()
                    example.allowed_devices.cache_clear()
                    await registry.close()

    async def walk_the_page(self) -> None:
        state = await example.redis_state()
        self.assertEqual(example.SiteFlags(), state.flags)
        self.assertEqual("dynamic_config_example_test:store:example_site_flags", state.flags_key)
        self.assertEqual([], state.devices)

        await example.update_flags(maintenance=True, banner="Upgrading", max_streams_per_user=5)
        self.assertEqual(example.SiteFlags(maintenance=True, banner="Upgrading", max_streams_per_user=5), (await example.redis_state()).flags)

        await example.write_banner_from_another_instance("Written elsewhere")
        self.assertEqual("Written elsewhere", (await example.redis_state()).flags.banner)

        refusal = await example.write_wrong_type()
        self.assertIsNotNone(refusal)
        self.assertIn("int", refusal or "")
        self.assertEqual(5, (await example.redis_state()).flags.max_streams_per_user, "the refused value was not written")

        devices = example.allowed_devices()
        self.assertTrue(await devices.add("tv-001"))
        self.assertTrue(await devices.contains("tv-001"))
        self.assertEqual("tv-001", await devices.random())
        self.assertTrue(await devices.remove("tv-001"))
        self.assertFalse(await devices.contains("tv-001"))
        await devices.add("tv-002")

        await example.restore_redis()
        restored = await example.redis_state()
        self.assertEqual(example.SiteFlags(), restored.flags)
        self.assertEqual([], restored.devices)


if __name__ == "__main__":
    unittest.main()
