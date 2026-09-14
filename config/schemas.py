"""Typed settings schema for the Oldman application."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from oldman.conf.schemas import (
    DatabaseConfig,
    DefaultSettings,
    FrontendConfig,
    I18nConfig,
    SessionConfig,
    StaticConfig,
    StorageBackendConfig,
    StoragesConfig,
    TemplateConfig,
    WebConfig,
)

BASE_DIR = Path(__file__).resolve().parents[1]


def default_template_config() -> TemplateConfig:
    """Return project template config."""
    return TemplateConfig(dir=BASE_DIR / "templates")


def default_static_config() -> StaticConfig:
    """Return project static config."""
    static_dir = BASE_DIR / "static"
    return StaticConfig(dir=static_dir, root=str(static_dir), url="/static")


def default_storages_config() -> StoragesConfig:
    """Return the project's isolated media filesystem storage."""
    return StoragesConfig(
        default=StorageBackendConfig(
            backend="oldman.storage.backends.filesystem.FileSystemStorage",
            options={"location": BASE_DIR / "media"},
        )
    )


def default_database_config() -> DatabaseConfig:
    """Return an isolated local SQLite database for the example."""
    database_path = BASE_DIR / "data" / "epg_dashboard.db"
    return DatabaseConfig(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")


def default_session_config() -> SessionConfig:
    """Return the Redis Session contract used by the Dashboard service."""
    return SessionConfig(
        enabled=True,
        expiry=86400,
        prefix="oldman_session:",
        user_prefix="oldman_user_session:",
        cookie_name="oldman_session_id",
    )


def default_web_config() -> WebConfig:
    """Assemble the Dashboard's browser-facing service settings."""
    return WebConfig(
        session=default_session_config(),
        template=default_template_config(),
        static=default_static_config(),
        frontend=FrontendConfig(),
    )


class Settings(DefaultSettings):
    """Project settings schema."""

    database: DatabaseConfig = Field(default_factory=default_database_config, description="Database settings")
    i18n: I18nConfig = Field(default_factory=I18nConfig, description="Internationalization settings")
    web: WebConfig = Field(default_factory=default_web_config, description="Web service settings")
    storages: StoragesConfig = Field(default_factory=default_storages_config, description="Named file storage settings")
