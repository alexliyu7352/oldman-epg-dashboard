"""Observe actual ImageCache files, hit expiry and failed WebP conversion."""

import asyncio
import io
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from PIL import Image
import typer

from oldman.cache.images import ImageCache
from oldman.cli import Command
from oldman.i18n import gettext_lazy as _
from oldman.providers.redis import redis_client
from oldman.storage import FileSystemStorage


def _png_bytes() -> bytes:
    """Produce a tiny owned fixture; ImageCache itself does not fetch a URL."""
    buffer = io.BytesIO()
    with Image.new("RGB", (64, 64), color=(40, 96, 160)) as source:
        source.save(buffer, "PNG")
    return buffer.getvalue()


class ImageCacheDemo(Command):
    """Keep all generated files and Redis metadata isolated from user uploads."""

    name = "image-cache"
    help = _("Inspect real image cache files, cache hits, expiry and WebP conversion failure.")

    async def handle(self) -> None:
        """Use explicit Storage and clean only this run's random Redis set and directory."""
        with tempfile.TemporaryDirectory(prefix="oldman-image-cache-demo-", dir="/tmp") as directory:
            storage = FileSystemStorage(directory)
            cache = ImageCache(storage=storage)
            token = uuid4().hex
            cache.cache_prefix = f"image_cache_{token}"
            cache.cache_set_key = f"oldman_epg_dashboard:examples:images:{token}"
            cache.cache_timeout = 1
            connection = None
            try:
                connection = await redis_client.using(cache.redis_alias).async_get_conn()
                content = await asyncio.to_thread(_png_bytes)
                first = await cache.cache_image("demo:first", content)
                assert set(first) == {"original", "webp"}
                async with await storage.open(first["original"]) as stored:
                    assert await stored.read() == content
                async with await storage.open(first["webp"]) as stored:
                    with Image.open(io.BytesIO(await stored.read())) as converted:
                        assert converted.format == "WEBP"
                        webp_size = converted.size
                score = await connection.zscore(cache.cache_set_key, first["original"])
                await asyncio.sleep(.05)
                assert await cache.get_cached_image("demo:first") == first
                assert await connection.zscore(cache.cache_set_key, first["original"]) > score
                await asyncio.sleep(1.1)
                assert await cache.get_cached_image("demo:first") is None
                assert all([await storage.exists(name) for name in first.values()])
                second = await cache.cache_image("demo:second", content)
                assert not any([await storage.exists(name) for name in first.values()])
                assert all([await storage.exists(name) for name in second.values()])
                damaged = await cache.cache_image("demo:invalid", b"not an image")
                assert set(damaged) == {"original"}
                async with await storage.open(damaged["original"]) as stored:
                    assert await stored.read() == b"not an image"
                typer.echo(json.dumps({
                    "storage_directory": directory, "logical_names": second, "webp_size": webp_size,
                    "original_bytes_match": True, "hit_extends_expiry": True,
                    "expired_read_misses": True, "next_write_cleans_files": True,
                    "invalid_bytes_keep_original_only": True,
                }))
            finally:
                try:
                    if connection is not None:
                        await connection.delete(cache.cache_set_key)
                finally:
                    await redis_client.close()
        assert not Path(directory).exists()
        typer.echo("Image Demo temporary files removed.")
