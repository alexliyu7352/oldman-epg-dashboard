"""Dynamic configuration examples: a typed YAML file through YamlStore, typed Redis values through RedisStore and a Redis set.

Each store is one instance per process, built on first use: the file path and the Redis alias come from settings,
and ``update()`` queues only within one instance. The demo keeps its Redis values under the CACHE alias because it
has no DEFAULT alias; a production service keeps runtime configuration in a Redis that does not evict keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from io import StringIO
from pathlib import Path
from typing import Any, cast

import msgspec
import oldman.conf as conf
from oldman.conf.base import new_yaml
from oldman.conf.containers import RedisSet, RedisStore, YamlStore
from oldman.providers.redis import redis_client
from oldman.serializers import MsgspecModel
from oldman.utils.files import atomic_write
from ruamel.yaml import YAML

CHECK_INTERVAL = 2.0
REDIS_ALIAS = "CACHE"
FILE_NAME = "example_channel_overrides.yaml"


# ---- The YAML file ----


class ChannelOverride(MsgspecModel):
    display_name: str = ""
    epg_offset_minutes: int = 0
    enabled: bool = True


def _sample_channels() -> dict[str, ChannelOverride]:
    return {
        "cctv1": ChannelOverride(display_name="CCTV-1"),
        "tvb-jade": ChannelOverride(display_name="TVB Jade", epg_offset_minutes=-60),
    }


class ChannelOverrides(MsgspecModel):
    announcement: str = ""
    channels: dict[str, ChannelOverride] = msgspec.field(default_factory=_sample_channels)


class ChannelOverridesStore(YamlStore[ChannelOverrides]):
    """Counts the outside edits it picked up: resetting what was derived from the old value is what the hook is for."""

    def __init__(self, path: Path) -> None:
        super().__init__(ChannelOverrides, path, check_interval=CHECK_INTERVAL)
        self.outside_changes = 0

    def on_file_changed(self, old: ChannelOverrides, new: ChannelOverrides) -> None:
        self.outside_changes += 1


@cache
def channel_overrides() -> ChannelOverridesStore:
    return ChannelOverridesStore(conf.settings.core.data_dir / FILE_NAME)


@dataclass(frozen=True)
class FileState:
    """What the file page shows: the value the store serves, how many outside edits it picked up, and the file itself."""

    value: ChannelOverrides
    outside_changes: int
    file_text: str


async def file_state() -> FileState:
    store = channel_overrides()
    value = await store.get()
    text = await asyncio.to_thread(store.config_path.read_text, encoding="utf-8")
    return FileState(value, store.outside_changes, text)


async def set_channel_offset(channel_id: str, minutes: int) -> None:
    """The usual way to change one entry: get(), assign, save()."""
    store = channel_overrides()
    value = await store.get()
    override = value.channels.get(channel_id) or ChannelOverride(display_name=channel_id)
    override.epg_offset_minutes = minutes
    value.channels[channel_id] = override
    await store.save(value)


async def set_announcement(text: str) -> None:
    """A top-level field in one line: update() reads, changes and saves."""
    await channel_overrides().update(announcement=text)


async def _edit_file_outside(change: Callable[[dict[str, Any]], None]) -> None:
    """Change the file the way an editor or a deploy tool would: read it as plain YAML, change it, replace it whole."""
    store = channel_overrides()
    await store.get()  # the store writes the file on first use
    text = await asyncio.to_thread(store.config_path.read_text, encoding="utf-8")
    data = YAML(typ="safe", pure=True).load(text) or {}
    change(data)
    buffer = StringIO()
    new_yaml().dump(data, buffer)
    # An editor writes through a symbolic link to the file it points at, as the store's own saves do.
    await asyncio.to_thread(atomic_write, store.config_path, buffer.getvalue(), follow_symlinks=True)


async def edit_announcement_outside(text: str) -> None:
    def change(data: dict[str, Any]) -> None:
        data["announcement"] = text

    await _edit_file_outside(change)


async def break_file_outside() -> None:
    """A typo an editor would let through: a word where the schema wants whole minutes."""

    def change(data: dict[str, Any]) -> None:
        channels = data.setdefault("channels", {})
        channel_id = next(iter(channels), "cctv1")
        channels.setdefault(channel_id, {})["epg_offset_minutes"] = "soon"

    await _edit_file_outside(change)


async def restore_file() -> None:
    await channel_overrides().save(ChannelOverrides())


# ---- Redis ----


class SiteFlags(MsgspecModel):
    maintenance: bool = False
    banner: str = ""
    max_streams_per_user: int = 3


SITE_FLAGS_NAME = "example_site_flags"
DEVICES_NAME = "example_allowed_devices"


@cache
def site_flags() -> RedisStore[SiteFlags]:
    return RedisStore(SiteFlags, SITE_FLAGS_NAME, client=redis_client.using(REDIS_ALIAS))


@cache
def allowed_devices() -> RedisSet[str]:
    return RedisSet(str, DEVICES_NAME, client=redis_client.using(REDIS_ALIAS))


@dataclass(frozen=True)
class RedisState:
    """What the Redis page shows after an operation."""

    flags: SiteFlags
    flags_key: str
    devices: list[str]
    devices_key: str


async def redis_state() -> RedisState:
    flags, devices = site_flags(), allowed_devices()
    return RedisState(await flags.get(), flags.key, sorted(await devices.members()), devices.key)


async def update_flags(*, maintenance: bool, banner: str, max_streams_per_user: int) -> None:
    await site_flags().update(maintenance=maintenance, banner=banner, max_streams_per_user=max_streams_per_user)


async def write_banner_from_another_instance(banner: str) -> None:
    """Another process holds its own store; a second instance stands in for it. Nothing is cached locally, so the next read sees it."""
    other = RedisStore(SiteFlags, SITE_FLAGS_NAME, client=redis_client.using(REDIS_ALIAS))
    await other.update(banner=banner)


async def write_wrong_type() -> str | None:
    """update() checks types before writing: a form's text never lands in an int field. Returns the refusal."""
    try:
        await site_flags().update(max_streams_per_user=cast(Any, "three"))
    except msgspec.ValidationError as error:
        return str(error)
    return None


async def restore_redis() -> None:
    await site_flags().save(SiteFlags())
    devices = allowed_devices()
    for device in await devices.members():
        await devices.remove(device)
