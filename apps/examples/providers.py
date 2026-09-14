"""Remote Select providers backed by the Dashboard example database."""

from __future__ import annotations

from typing import Any

from markupsafe import Markup, escape
from sqlalchemy import select

from oldman.web.components.selects import DataSelectProvider, ModelSelectProvider, SelectChoice, select_registry

from .models import ExampleLogo, ExampleTag


def _is_staff_request(request: Any) -> bool:
    """Allow provider reads only for the authenticated Dashboard staff session."""
    session = getattr(getattr(request, "ctx", None), "session", None)
    return bool(session and session.is_authenticated() and getattr(session, "is_staff", False))


@select_registry.register("example_logos")
class ExampleLogoProvider(ModelSelectProvider):
    """Search available local SVG logos with optional country filtering."""

    model = ExampleLogo
    search_fields = ("name", "slug", "country_code")
    page_size = 8

    async def check_auth(self, request: Any, context: Any) -> bool:
        """Require the same staff identity as the surrounding example page."""
        return _is_staff_request(request)

    async def get_queryset(self, request: Any, context: Any):
        """Return stable, available Logo rows."""
        return select(ExampleLogo).where(ExampleLogo.is_available.is_(True)).order_by(ExampleLogo.name.asc(), ExampleLogo.id.asc())

    async def filter_queryset(self, request: Any, context: Any, query: Any, term: str, depends: dict[str, str]):
        """Apply the signed country dependency after the shared text search."""
        query = await super().filter_queryset(request, context, query, term, depends)
        country_code = depends.get("country_code")
        return query.where(ExampleLogo.country_code == country_code) if country_code else query

    def get_option(self, obj: ExampleLogo) -> SelectChoice:
        """Return accessible text plus trusted rich HTML from local fixture fields."""
        text = f"{obj.name} · {obj.country_code}"
        html = Markup(
            '<span class="flex items-center gap-3">'
            f'<img src="{escape(obj.svg_path)}" alt="" width="36" height="36" loading="lazy" '
            'class="size-9 shrink-0 rounded bg-default-50 object-contain">'
            '<span class="min-w-0">'
            f'<strong class="block truncate">{escape(obj.name)}</strong>'
            f'<small class="text-default-500">{escape(obj.country_code)} · {escape(obj.slug)}</small>'
            "</span></span>"
        )
        return SelectChoice(id=obj.id, text=text, html=html, data={"country_code": obj.country_code})


@select_registry.register("example_tags")
class ExampleTagProvider(ModelSelectProvider):
    """Search the existing ExampleTag fixture for multi-select examples."""

    model = ExampleTag
    search_fields = ("name", "slug")
    page_size = 6

    async def check_auth(self, request: Any, context: Any) -> bool:
        """Require the same staff identity as the surrounding example page."""
        return _is_staff_request(request)

    async def get_queryset(self, request: Any, context: Any):
        """Return tags in stable display order."""
        return select(ExampleTag).order_by(ExampleTag.name.asc(), ExampleTag.id.asc())


@select_registry.register("example_countries")
class ExampleCountryProvider(DataSelectProvider):
    """Finite country choices showing the non-database provider boundary."""

    page_size = 10

    async def check_auth(self, request: Any, context: Any) -> bool:
        """Require an authenticated staff session."""
        return _is_staff_request(request)

    async def get_choices(self, request: Any, context: Any):
        """Return the fixture's finite country set."""
        return (("US", "United States"), ("GB", "United Kingdom"), ("CA", "Canada"), ("AU", "Australia"))


__all__ = ["ExampleCountryProvider", "ExampleLogoProvider", "ExampleTagProvider"]
