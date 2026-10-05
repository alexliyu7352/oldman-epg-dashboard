"""Access tokens for programs: obtain, refresh and revoke, the framework's TokenFlow (the token example uses them).

The skeleton has no tokens; this file is what this project adds. Tokens go to the same accounts as the sign-in page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oldman.web.auth import TokenFlow

if TYPE_CHECKING:
    from oldman.web.routing import WebApp


# Every active account, like the sign-in page: the active check is every login's floor, nothing is added on top.
token_flow = TokenFlow(accept_user=lambda _user: True)


def install_token_routes(app: WebApp) -> None:
    """Register the three token routes; the signing key is web.auth.jwt.secret, asked for at the first request."""
    token_flow.register_routes(
        app,
        obtain_path="/api/token",
        refresh_path="/api/token/refresh",
        revoke_path="/api/token/revoke",
    )
