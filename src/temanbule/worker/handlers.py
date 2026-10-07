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
JOB_PURPOSE_FACT_EXTRACTION = "user_fact_extraction"
JOB_PURPOSE_ASSESSMENT = "learning_assessment"
JOB_PURPOSE_PODCAST_INGESTION = "podcast_document_ingestion"
FACTS_FLOW_VERSION = "fact-ext.v1"
ASSESSMENT_FLOW_VERSION = "assessment.v1"
PODCAST_INGESTION_FLOW_VERSION = "podcast-ing.v1"

# Interval poll ulang job Langflow background (detik dari sekarang).
PODCAST_INGESTION_POLL_INTERVAL_SECONDS = 10
PODCAST_INGESTION_MAX_POLLS = 180  # ≈ 30 menit batas ingestion dokumen


async def handle_podcast_source_uploaded(session: AsyncSession, payload: dict[str, Any]) -> None:
    """podcast.source_uploaded.v1 → dispatch ingestion Langflow (background).

    Membuat SATU SQL job per (source_version, flow_version) — dedupe_key
    mencegah dispatch ganda pada delivery ulang event. Job membawa langkah
    ``dispatch`` (trigger Langflow, simpan vendor job_id) lalu ``poll``
    berkala sampai terminal; kedua langkah idempoten sehingga retry aman.
    """
    from temanbule.modules.podcasts.models import Podcast, PodcastSourceVersion

    podcast_id = str(payload.get("podcast_id", ""))
    source_version_id = str(payload.get("source_version_id", ""))
    owner_user_id = str(payload.get("owner_user_id", ""))
    media_id = str(payload.get("media_id", ""))
    storage_key = str(payload.get("storage_key", ""))
    checksum = str(payload.get("checksum", ""))
    if not podcast_id or not source_version_id or not owner_user_id or not media_id:
        raise NotFoundError("Payload event podcast tidak lengkap.")

    source = (
        await session.execute(
            select(PodcastSourceVersion).where(PodcastSourceVersion.id == source_version_id)
        )
    ).scalar_one_or_none()
    podcast = (
        await session.execute(select(Podcast).where(Podcast.id == podcast_id))
    ).scalar_one_or_none()
    if source is None or podcast is None or podcast.user_id != owner_user_id:
        raise NotFoundError("Podcast source tidak ditemukan.")
    if source.parse_status in {"parsed", "failed"}:
        return  # sudah terminal; tidak ada dispatch baru

    dedupe_key = f"podcast-ingest:{source_version_id}:{PODCAST_INGESTION_FLOW_VERSION}"
    existing = (
        await session.execute(
            select(BackgroundJob.id).where(BackgroundJob.dedupe_key == dedupe_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        BackgroundJob(
            id=new_ulid(),
            dedupe_key=dedupe_key,
            purpose=JOB_PURPOSE_PODCAST_INGESTION,
            run_after=datetime.now(UTC),
            payload=json.dumps(
                {
                    "step": "dispatch",
                    "podcast_id": podcast_id,
                    "source_version_id": source_version_id,
                    "owner_user_id": owner_user_id,
                    "media_id": media_id,
                    "storage_key": storage_key,
                    "checksum": checksum,
                    "flow_version": PODCAST_INGESTION_FLOW_VERSION,
                },
                sort_keys=True,
            ),
        )
    )
    await session.flush()


async def handle_extraction_committed(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Extraction canonical committed → facts & assessment jobs independen.

    Idempoten per (extraction, flow); kedua job punya checkpoint/retry sendiri
    (langflow-flows.md: masing-masing punya checkpoint dan retry sendiri).
    """
    extraction_id = str(payload.get("extraction_id", ""))
    session_id = str(payload.get("session_id", ""))
    owner_user_id = str(payload.get("owner_user_id", ""))
    source_start = payload.get("source_start")
    source_end = payload.get("source_end")
    if not extraction_id or not session_id or not owner_user_id:
        raise NotFoundError("Payload event tidak lengkap.")
    if not isinstance(source_start, int) or not isinstance(source_end, int):
        raise NotFoundError("Payload event tidak lengkap (range).")

    from temanbule.modules.conversations.models import ConversationExtraction

    extraction = (
        await session.execute(
            select(ConversationExtraction).where(
                ConversationExtraction.id == extraction_id
            )
        )
    ).scalar_one_or_none()
    if extraction is None or extraction.owner_user_id != owner_user_id:
        raise NotFoundError("Extraction tidak ditemukan.")

    now = datetime.now(UTC)
    for purpose, flow_version in (
        (JOB_PURPOSE_FACT_EXTRACTION, FACTS_FLOW_VERSION),
        (JOB_PURPOSE_ASSESSMENT, ASSESSMENT_FLOW_VERSION),
    ):
        dedupe_key = f"{purpose}:{extraction_id}:{flow_version}"
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
                    purpose=purpose,
                    run_after=now,
                    payload=json.dumps(
                        {
                            "extraction_id": extraction_id,
                            "session_id": session_id,
                            "owner_user_id": owner_user_id,
                            "source_start": source_start,
                            "source_end": source_end,
                            "flow_version": flow_version,
                        },
                        sort_keys=True,
                    ),
                )
            )
    await session.flush()


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
    "conversation.extraction_committed.v1": handle_extraction_committed,
    "podcast.source_uploaded.v1": handle_podcast_source_uploaded,
}


async def handle_auth_email_requested_bound(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Binding settings untuk handler email; dispatcher memanggil tanpa settings."""
    from temanbule.platform.settings import load_settings
    from temanbule.worker.auth_email_handler import handle_auth_email_requested

    await handle_auth_email_requested(session, payload, load_settings(validate=False))


EVENT_HANDLERS["auth.email_requested.v1"] = handle_auth_email_requested_bound
