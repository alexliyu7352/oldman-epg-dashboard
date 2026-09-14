"""Exercise the HTTP example against an owned TCP upstream, not mocked responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from apps.examples import http_example as example
from apps.examples.settings import ExamplesSettings
from pydantic import ValidationError

from oldman.contrib.http import ClientType, MultiHttpClient


class HTTPExampleTests(unittest.IsolatedAsyncioTestCase):
    """Keep sockets, timeouts and stream closure real in one small lifecycle check."""

    async def test_requests_failures_and_cleanup(self) -> None:
        """Check reuse, status handling, malformed data, stream limits and cancellation."""
        mode = "normal"
        requests: list[str] = []
        connections = 0
        pending: set[asyncio.Task] = set()
        delayed = asyncio.Event()

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """Serve just the four upstream operations over persistent HTTP/1.1."""
            nonlocal connections
            connections += 1
            task = asyncio.current_task()
            assert task is not None
            pending.add(task)
            try:
                while True:
                    headers = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
                    requests.append(headers)
                    path = urlsplit(headers.split(" ", 2)[1]).path
                    status = 404 if path == "/status/404" else 200
                    content_type = "application/json"
                    body = json.dumps({"message": "<em>network data</em>", "path": path}).encode()
                    if mode == "redirect":
                        status = 302
                    elif mode == "invalid":
                        body = b"not JSON"
                    if path == "/delay/10":
                        delayed.set()
                        await asyncio.sleep(0.3)
                    if path.startswith("/stream-bytes/"):
                        content_type = "application/octet-stream"
                        body = b"x" * (example.STREAM_LIMIT + 4096 if mode == "large" else 65536)
                    writer.write(
                        f"HTTP/1.1 {status} Result\r\nContent-Type: {content_type}\r\n"
                        "Location: /get\r\nTransfer-Encoding: chunked\r\n\r\n".encode()
                    )
                    for offset in range(0, len(body), 4096):
                        chunk = body[offset:offset + 4096]
                        writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        await writer.drain()
                    writer.write(b"0\r\n\r\n")
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass  # The consumer deliberately closes timed-out/limited streams.
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                pending.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = ExamplesSettings(http_base_url=f"http://127.0.0.1:{port}")
        client = MultiHttpClient(client_type=ClientType.HTTPX, retry_count=0, max_connections=2,
                                 content_decoding=True, user_agent="Oldman-HTTP-Test")
        pool = None
        try:
            await client.init_client()
            self.assertEqual(connections, 0)  # Initialization must not connect.
            with patch.object(example, "http_client", client), patch.object(
                example, "app", SimpleNamespace(settings=settings),
            ):
                result = await example.run_http_example("json")
                self.assertEqual((result.outcome, result.status_code), ("success", 200))
                self.assertIn("<em>network data</em>", result.json_text)
                self.assertIn("example=oldman-epg-dashboard", requests[-1])
                self.assertNotIn("\r\ncookie:", requests[-1].lower())
                pool = client.client.http_client
                await example.run_http_example("json")
                self.assertEqual(connections, 1)  # The second request reused the TCP connection.
                self.assertIs(pool, client.client.http_client)

                result = await example.run_http_example("status")
                self.assertEqual((result.outcome, result.status_code), ("http_error", 404))
                mode = "redirect"
                count = len(requests)
                result = await example.run_http_example("json")
                self.assertEqual((result.outcome, result.status_code), ("http_error", 302))
                self.assertEqual(len(requests), count + 1)
                mode = "invalid"
                self.assertEqual((await example.run_http_example("json")).outcome, "invalid_content")

                mode = "normal"
                result = await example.run_http_example("stream")
                self.assertEqual((result.outcome, result.byte_count), ("success", 65536))
                self.assertEqual(result.sha256, hashlib.sha256(b"x" * 65536).hexdigest())
                mode = "large"
                result = await example.run_http_example("stream")
                self.assertEqual((result.outcome, result.sha256), ("too_large", ""))
                self.assertGreater(result.byte_count, example.STREAM_LIMIT)

                mode = "normal"
                count = len(requests)
                with patch.object(example, "REQUEST_TIMEOUT", 0.05):
                    result = await example.run_http_example("timeout")
                self.assertEqual(result.outcome, "timeout")
                self.assertLess(result.elapsed_ms, 500)
                self.assertEqual(len(requests), count + 1)
                delayed.clear()
                request = asyncio.create_task(example.run_http_example("timeout"))
                await asyncio.wait_for(delayed.wait(), 1)
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                self.assertEqual(client.active_requests, 0)
                self.assertEqual((await example.run_http_example("json")).outcome, "success")
                with self.assertRaises(KeyError):
                    await example.run_http_example("not-an-operation")
                with patch.object(client, "get", side_effect=TypeError("application bug")):
                    with self.assertRaises(TypeError):
                        await example.run_http_example("json")

                server.close()
                await client.close_client()
                await server.wait_closed()
                self.assertTrue(pool.is_closed)
                await client.init_client()
                self.assertEqual((await example.run_http_example("json")).outcome, "connection_error")
        finally:
            await client.close_client()
            server.close()
            for task in list(pending):
                task.cancel()
            await asyncio.gather(*list(pending), return_exceptions=True)
            await server.wait_closed()
        self.assertFalse(client.is_initialized())

        for invalid in ("file:///etc/passwd", "https://user:secret@example.com", "https://example.com?x=1"):
            with self.assertRaises(ValidationError):
                ExamplesSettings(http_base_url=invalid)
