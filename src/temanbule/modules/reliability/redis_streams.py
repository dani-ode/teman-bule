"""Redis Streams publisher/dispatcher untuk outbox (FND-05).

At-least-once delivery; consumer ack setelah durable result.
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis

from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


class RedisOutboxPublisher:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = redis.from_url(  # type: ignore[no-untyped-call]
                self._settings.redis_url, decode_responses=True
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            msg = "Redis client belum terhubung; panggil connect() terlebih dahulu"
            raise RuntimeError(msg)
        return self._client

    async def publish(self, *, event_id: str, event_type: str, payload: str) -> None:
        client = self._require_client()
        await client.xadd(
            self._settings.redis_outbox_stream,
            {"event_id": event_id, "event_type": event_type, "payload": payload},
        )


class RedisStreamDispatcher:
    """Consumer group dengan retry dan dead-letter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = redis.from_url(  # type: ignore[no-untyped-call]
                self._settings.redis_url, decode_responses=True
            )
        await self._ensure_group()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            msg = "Redis client belum terhubung; panggil connect() terlebih dahulu"
            raise RuntimeError(msg)
        return self._client

    async def _ensure_group(self) -> None:
        client = self._require_client()
        try:
            await client.xgroup_create(
                self._settings.redis_outbox_stream,
                self._settings.redis_consumer_group,
                id="0",
                mkstream=True,
            )
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_batch(self, *, count: int = 10) -> list[tuple[str, dict[str, Any]]]:
        client = self._require_client()
        result = await client.xreadgroup(
            self._settings.redis_consumer_group,
            self._settings.redis_consumer_name,
            {self._settings.redis_outbox_stream: ">"},
            count=count,
            block=self._settings.redis_block_ms,
        )
        if not result:
            return []
        messages: list[tuple[str, dict[str, Any]]] = []
        for _stream, entries in result:
            for entry_id, fields in entries:
                messages.append((entry_id, fields))
        return messages

    async def ack(self, entry_id: str) -> None:
        client = self._require_client()
        await client.xack(
            self._settings.redis_outbox_stream,
            self._settings.redis_consumer_group,
            entry_id,
        )

    async def dead_letter(self, entry_id: str, fields: dict[str, Any], *, reason: str) -> None:
        client = self._require_client()
        await client.xadd(
            self._settings.redis_dead_letter_stream,
            {
                "original_id": entry_id,
                "reason": reason,
                "event_id": fields.get("event_id", ""),
                "event_type": fields.get("event_type", ""),
            },
        )
        await self.ack(entry_id)
