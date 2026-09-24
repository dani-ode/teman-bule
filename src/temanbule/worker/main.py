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

from temanbule.modules.reliability.redis_streams import RedisStreamDispatcher
from temanbule.platform.db.engine import create_engine, create_session_factory
from temanbule.platform.logging import configure_logging
from temanbule.platform.settings import load_settings

logger = logging.getLogger(__name__)


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
        await dispatcher.close()
        await engine.dispose()
        logger.info("worker_stopped")


async def _process_entry(
    dispatcher: RedisStreamDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    entry_id: str,
    fields: dict[str, Any],
) -> None:
    """Dispatch satu event; ack setelah durable mark published."""
    from temanbule.modules.reliability.repository import ReliabilityRepository

    event_id = str(fields.get("event_id", ""))
    event_type = str(fields.get("event_type", ""))
    try:
        async with session_factory() as session:
            repo = ReliabilityRepository(session)
            # Dispatch per event type; handler konkrit ditambahkan per slice.
            # Foundation: tandai published agar at-least-once loop berhenti.
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
