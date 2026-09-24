"""Event handlers untuk durable worker (Phase 3).

Kontrak:
- Handler dipanggil dari outbox dispatcher; setiap handler idempoten karena
  delivery at-least-once.
- conversation.session_completed.v1 → buat SATU ingestion job (dedupe_key
  per session+range+flow); job processor dijalankan terpisah oleh job runner.
- Handler tidak memanggil vendor langsung; SQL job tetap pemilik retry.
- Payload event hanya reference; data otoritatif dibaca ulang dari DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.models import ConversationMessage, ConversationSession
from temanbule.modules.knowledge.services import JOB_PURPOSE_INGESTION
from temanbule.modules.reliability.models import BackgroundJob
from temanbule.platform.errors import NotFoundError
from temanbule.platform.security import new_ulid

INGESTION_FLOW_VERSION = "conv-ing.v1"
INGESTION_RANGE_SIZE = 50


async def handle_session_completed(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Buat ingestion job untuk rentang pesan session yang selesai.

    Idempoten: dedupe_key per (session, range, flow_version) mencegah
    job ganda pada delivery ulang event.
    """
    session_id = str(payload.get("session_id", ""))
    owner_user_id = str(payload.get("owner_user_id", ""))
    if not session_id or not owner_user_id:
        raise NotFoundError("Payload event tidak lengkap.")

    conversation = (
        await session.execute(
            select(ConversationSession).where(ConversationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if conversation is None or conversation.user_id != owner_user_id:
        raise NotFoundError("Session tidak ditemukan.")

    max_sequence = (
        await session.execute(
            select(func.coalesce(func.max(ConversationMessage.sequence), 0)).where(
                ConversationMessage.session_id == session_id
            )
        )
    ).scalar_one()
    if max_sequence == 0:
        return

    now = datetime.now(UTC)
    # Rentang sederhana: satu job per blok INGESTION_RANGE_SIZE pesan.
    start = 1
    while start <= max_sequence:
        end = min(start + INGESTION_RANGE_SIZE - 1, max_sequence)
        dedupe_key = f"ingest:{session_id}:{start}:{end}:{INGESTION_FLOW_VERSION}"
        existing = (
            await session.execute(
                select(BackgroundJob.id).where(BackgroundJob.dedupe_key == dedupe_key)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                BackgroundJob(
                    id=new_ulid(),
                    dedupe_key=dedupe_key,
                    purpose=JOB_PURPOSE_INGESTION,
                    run_after=now,
                    payload=json.dumps(
                        {
                            "session_id": session_id,
                            "owner_user_id": owner_user_id,
                            "source_start": start,
                            "source_end": end,
                            "flow_version": INGESTION_FLOW_VERSION,
                        },
                        sort_keys=True,
                    ),
                )
            )
        start = end + 1
    await session.flush()


EVENT_HANDLERS: dict[str, Any] = {
    "conversation.session_completed.v1": handle_session_completed,
}
