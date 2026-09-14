"""Runtime settings instance for the Oldman application."""

from typing import cast

import oldman.conf as conf
from config.schemas import Settings

settings = cast(Settings, conf.settings)
