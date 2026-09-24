"""Health endpoints: liveness dan readiness (FND-02)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from temanbule.platform.settings import redacted_settings_snapshot

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    """Readiness: cek koneksi DB dan Redis. Tidak membocorkan secret."""
    settings = request.app.state.settings
    checks: dict[str, str] = {}

    try:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    overall = "ready" if all(v == "ok" for v in checks.values()) else "degraded"
    return {
        "status": overall,
        "checks": checks,
        "features": {
            "ai": settings.feature_ai_enabled,
            "google_auth": settings.feature_google_auth_enabled,
            "billing": settings.feature_billing_enabled,
            "realtime_call": settings.feature_realtime_call_enabled,
            "podcast": settings.feature_podcast_enabled,
        },
        "config": redacted_settings_snapshot(settings),
    }
