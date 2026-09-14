"""Configuration package for the oldman application."""

from typing import Any

settings: Any
__all__ = ["settings"]


def __getattr__(name: str) -> Any:
    """Lazily expose runtime settings."""
    if name == "settings":
        from .settings import settings

        return settings
    raise AttributeError(name)
