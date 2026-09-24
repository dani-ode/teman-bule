"""Audit service: append-only events, teredaksi."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.reliability.models import AuditEvent
from temanbule.platform.security import new_ulid

# Keys yang tidak pernah masuk metadata audit
_FORBIDDEN_KEYS = {"password", "api_key", "token", "secret", "authorization", "credential"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if k.lower() in _FORBIDDEN_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def record_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target: str | None = None,
    result: str = "success",
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Catat audit event dalam transaksi yang sama dengan domain mutation."""
    from datetime import UTC, datetime

    event = AuditEvent(
        id=new_ulid(),
        actor=actor,
        action=action,
        target=target,
        result=result,
        correlation_id=correlation_id,
        metadata_redacted=json.dumps(_redact(metadata or {}), sort_keys=True),
        created_at=datetime.now(UTC),
    )
    session.add(event)
