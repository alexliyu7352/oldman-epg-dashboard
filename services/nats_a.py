"""First independently managed Core receiver; identity comes from its YAML."""

import asyncio

from oldman.runtime import SimpleApplication


class NatsAService(SimpleApplication):
    """Registry and Simple lifecycle own all subscriber and bus initialization."""

    def prepare(self) -> None:
        """No additional setup; do not import events or connect clients here."""

    async def main(self) -> None:
        """Stay alive until the normal CLI stop or signal cancels the service."""
        await asyncio.Event().wait()

