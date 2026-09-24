"""Generic idempotency service (FND-04).

Key+payload sama → replay response tersimpan. Key sama, payload berbeda → 409.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.reliability.models import IdempotencyRecord
from temanbule.platform.errors import IdempotencyConflictError
from temanbule.platform.security import new_ulid


def hash_request_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class StoredResponse:
    status: int
    body: str


class IdempotencyService:
    def __init__(self, session: AsyncSession, *, ttl_hours: int) -> None:
        self.session = session
        self.ttl_hours = ttl_hours

    async def find_replay(
        self, *, principal: str, operation: str, key: str, request_hash: str
    ) -> StoredResponse | None:
        from sqlalchemy import select

        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.principal == principal,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key sudah dipakai dengan payload berbeda."
            )
        if record.response_status is None:
            # Request sedang diproses atau gagal sebelum response; biarkan handler memutuskan.
            return None
        return StoredResponse(status=record.response_status, body=record.response_body or "")

    def reserve(
        self, *, principal: str, operation: str, key: str, request_hash: str
    ) -> IdempotencyRecord:
        now = datetime.now(UTC)
        record = IdempotencyRecord(
            id=new_ulid(),
            principal=principal,
            operation=operation,
            key=key,
            request_hash=request_hash,
            response_status=None,
            response_body=None,
            expires_at=now + timedelta(hours=self.ttl_hours),
            created_at=now,
        )
        self.session.add(record)
        return record

    async def store_response(
        self, record: IdempotencyRecord, *, status: int, body: str
    ) -> None:
        record.response_status = status
        record.response_body = body
