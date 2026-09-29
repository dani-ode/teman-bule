"""Durable worker entry point (FND-05).

Memproses outbox events dari Redis Streams secara at-least-once.
Handler per event type didaftarkan terpisah; unknown event type di-dead-letter.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from temanbule.modules.reliability.redis_streams import (
    RedisOutboxPublisher,
    RedisStreamDispatcher,
)
from temanbule.modules.reliability.repository import ReliabilityRepository
from temanbule.platform.db.engine import create_engine, create_session_factory
from temanbule.platform.logging import configure_logging
from temanbule.platform.settings import load_settings

logger = logging.getLogger(__name__)


async def run_outbox_relay(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    publisher: RedisOutboxPublisher,
    shutdown: asyncio.Event,
    poll_interval_seconds: float = 1.0,
) -> None:
    """Relay outbox DB → Redis Stream (at-least-once).

    Membaca outbox_events yang belum published lalu XADD ke stream. Event
    tetap di DB sampai dispatcher ack + mark published, sehingga crash di
    tengah tidak menghilangkan event (durable outbox pattern).
    """
    logger.info("outbox_relay_started")
    while not shutdown.is_set():
        try:
            async with session_factory() as session:
                repo = ReliabilityRepository(session)
                events = await repo.get_unpublished_outbox_events(limit=100)
                for event in events:
                    await publisher.publish(
                        event_id=event.event_id,
                        event_type=event.event_type,
                        payload=event.payload,
                    )
                # Tidak commit perubahan; mark published dilakukan dispatcher
                # setelah ack. Relay hanya membaca + memforward.
        except Exception as exc:
            logger.error(
                "outbox_relay_failed",
                extra={"error_code": type(exc).__name__},
            )
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            pass
    logger.info("outbox_relay_stopped")


async def run_worker() -> None:
    settings = load_settings(validate=True)
    configure_logging(settings.app_log_level)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    dispatcher = RedisStreamDispatcher(settings)
    await dispatcher.connect()

    shutdown = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("worker_shutdown_requested")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("worker_started", extra={"consumer": settings.redis_consumer_name})

    # Adapter vendor untuk background jobs (ingestion + embedding). Dibangun
    # hanya bila feature terkait aktif; job runner gagal eksplisit per job
    # bila adapter yang dibutuhkan tidak terpasang (bukan fake success).
    extractor = None
    embedder = None
    if settings.feature_ai_enabled:
        from temanbule.modules.knowledge.langflow_adapter import build_extraction_adapter

        try:
            extractor = build_extraction_adapter(settings, session_factory)
        except ValueError as exc:
            logger.warning(
                "extraction_adapter_unconfigured",
                extra={"error_code": "LANGFLOW_CONFIG_INCOMPLETE", "detail": str(exc)},
            )
    if settings.feature_dual_embedding_enabled:
        from temanbule.modules.knowledge.embedding_adapter import build_embedding_adapter

        try:
            embedder = build_embedding_adapter(settings)
        except ValueError as exc:
            logger.warning(
                "embedding_adapter_unconfigured",
                extra={"error_code": "EMBEDDING_CONFIG_INCOMPLETE", "detail": str(exc)},
            )

    from temanbule.worker.job_runner import run_job_loop

    publisher = RedisOutboxPublisher(settings)
    await publisher.connect()

    job_loop = asyncio.create_task(
        run_job_loop(
            settings=settings,
            session_factory=session_factory,
            extractor=extractor,
            embedder=embedder,
            shutdown=shutdown,
        )
    )
    relay = asyncio.create_task(
        run_outbox_relay(
            session_factory=session_factory,
            publisher=publisher,
            shutdown=shutdown,
        )
    )

    try:
        while not shutdown.is_set():
            try:
                batch = await dispatcher.read_batch(count=10)
            except Exception as exc:
                logger.error(
                    "dispatcher_read_failed",
                    extra={"error_code": type(exc).__name__},
                )
                await asyncio.sleep(2)
                continue

            for entry_id, fields in batch:
                await _process_entry(dispatcher, session_factory, entry_id, fields)
    finally:
        shutdown.set()
        await asyncio.gather(job_loop, relay, return_exceptions=True)
        await publisher.close()
        await dispatcher.close()
        await engine.dispose()
        logger.info("worker_stopped")


async def _process_entry(
    dispatcher: RedisStreamDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    entry_id: str,
    fields: dict[str, Any],
) -> None:
    """Dispatch satu event ke handler terdaftar; ack setelah durable mark published."""
    import json

    from temanbule.modules.reliability.repository import ReliabilityRepository
    from temanbule.worker.handlers import EVENT_HANDLERS

    event_id = str(fields.get("event_id", ""))
    event_type = str(fields.get("event_type", ""))
    try:
        async with session_factory() as session:
            handler = EVENT_HANDLERS.get(event_type)
            if handler is not None:
                raw_payload = fields.get("payload", "{}")
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
                await handler(session, payload)
            repo = ReliabilityRepository(session)
            await repo.mark_outbox_published(event_id)
            await session.commit()
        await dispatcher.ack(entry_id)
    except Exception as exc:
        logger.error(
            "event_dispatch_failed",
            extra={"event_type": event_type, "error_code": type(exc).__name__},
        )
        await dispatcher.dead_letter(entry_id, fields, reason=type(exc).__name__)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
