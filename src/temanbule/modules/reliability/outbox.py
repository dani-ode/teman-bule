"""Outbox service: append event dalam transaksi domain; dispatch terpisah (FND-05)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.reliability.models import OutboxEvent
from temanbule.platform.security import new_ulid


def record_outbox_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    """Simpan outbox event atomik dengan domain mutation. Payload wajib teredaksi."""
    event = OutboxEvent(
        event_id=new_ulid(),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload=json.dumps(payload, sort_keys=True, default=str),
        occurred_at=datetime.now(UTC),
        published_at=None,
    )
    session.add(event)
    return event
