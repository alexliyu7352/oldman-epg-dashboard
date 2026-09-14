"""Second receiver uses the same App, with monitor_b in its own configuration."""

import asyncio

from oldman.db import db_manager
from oldman.runtime import SimpleApplication


class NatsBService(SimpleApplication):
    """Direct inheritance keeps normal filename-based service discovery."""

    def prepare(self) -> None:
        """The App Registry already knows which events module to load."""

    async def main(self) -> None:
        """Run until the ordinary lifecycle cancels this wait."""
        await asyncio.Event().wait()

    async def after_stop(self) -> None:
        """Close the database only after framework-managed handlers exit."""
        try:
            await db_manager.close()
        finally:
            await super().after_stop()
