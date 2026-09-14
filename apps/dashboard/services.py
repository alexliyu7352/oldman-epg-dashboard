"""Dashboard 页面聚合服务。"""

from __future__ import annotations

from typing import Any

from apps.epg_admin import services as epg_services


async def dashboard_stats() -> dict[str, Any]:
    """返回 Dashboard 页面展示的真实业务统计。"""
    return await epg_services.dashboard_stats()


async def dashboard_notifications(limit: int = 8) -> list[dict[str, Any]]:
    """返回 Dashboard 顶栏使用的真实业务通知。"""
    return await epg_services.dashboard_notifications(limit=limit)
