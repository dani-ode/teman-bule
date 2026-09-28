"""Wiring handler tool ingestion/TOEFL/podcast ke ToolExecutionService.

Kontrak:
- conversation.persist_extraction: append-only, dedupe per session/range/schema/
  flow version; tidak dapat menulis ulang pesan mentah.
- toefl.record_evaluation: delegasi ke ToeflService yang memvalidasi rubric
  pinned, evidence kutipan jawaban, dan state attempt secara otoritatif.
- podcast.get_source_context: hanya chunk milik podcast yang dimiliki owner,
  dokumen published dan tidak tombstoned; tanpa arbitrary vector filter.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.assessments.services import ToeflService
from temanbule.modules.conversations.models import ConversationExtraction
from temanbule.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from temanbule.modules.podcasts.models import PodcastSourceVersion
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid


async def conversation_persist_extraction_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    source_message_ids: list[str] = arguments["source_message_ids"]
    extraction: dict[str, Any] = arguments["extraction"]
    evidence_ids: list[str] = extraction["evidence_message_ids"]

    if not set(evidence_ids) <= set(source_message_ids):
        raise ValidationError(
            "evidence_message_ids harus subset dari source_message_ids.",
            details=[{"field": "extraction.evidence_message_ids", "message": "bukan subset"}],
        )

    # Verifikasi semua pesan sumber milik session yang dimiliki owner.
    from temanbule.modules.conversations.models import ConversationMessage

    rows = (
        await session.execute(
            select(ConversationMessage).where(
                ConversationMessage.id.in_(source_message_ids)
            )
        )
    ).scalars().all()
    if len(rows) != len(set(source_message_ids)):
        raise NotFoundError("Sebagian source message tidak ditemukan.")
    session_ids = {row.session_id for row in rows}
    if len(session_ids) != 1:
        raise ValidationError(
            "Semua source message harus berasal dari satu session.",
            details=[{"field": "source_message_ids", "message": "multi-session"}],
        )
    session_id = session_ids.pop()
    for row in rows:
        if row.owner_user_id != owner_user_id:
            raise NotFoundError("Source message di luar kepemilikan owner.")

    # Dedupe session/range/schema/flow version (constraint DB juga menegakkan).
    sequences = sorted(row.sequence for row in rows)
    source_start, source_end = sequences[0], sequences[-1]
    flow_version = str(extraction.get("flow_version", "ingestion.v1"))
    existing = (
        await session.execute(
            select(ConversationExtraction).where(
                ConversationExtraction.session_id == session_id,
                ConversationExtraction.source_start == source_start,
                ConversationExtraction.source_end == source_end,
                ConversationExtraction.schema_version == extraction["schema_version"],
                ConversationExtraction.flow_version == flow_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.data != json.dumps(extraction, sort_keys=True):
            raise ConflictError(
                "Extraction berbeda sudah tersimpan untuk range yang sama.",
                code="EXTRACTION_CONFLICT",
            )
        return {"resource_id": existing.id, "resource_version": 1, "deduplicated": True}

    record = ConversationExtraction(
        id=new_ulid(),
        session_id=session_id,
        owner_user_id=owner_user_id,
        source_start=source_start,
        source_end=source_end,
        schema_version=extraction["schema_version"],
        flow_version=flow_version,
        data=json.dumps(extraction, sort_keys=True),
    )
    session.add(record)
    await session.flush()
    return {"resource_id": record.id, "resource_version": 1, "deduplicated": False}


async def toefl_record_evaluation_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    # Rubric pinned dan evidence kutipan divalidasi otoritatif oleh service;
    # dimensions dari tool harus integer skor per rubric writing practice.
    raw_dimensions: dict[str, Any] = arguments["dimensions"]
    dimensions: dict[str, int] = {}
    for key, value in raw_dimensions.items():
        if isinstance(value, float) and not value.is_integer():
            raise ValidationError(
                "Dimensi skor writing wajib bilangan bulat sesuai rubric.",
                details=[{"field": f"dimensions.{key}", "message": "harus integer"}],
            )
        dimensions[key] = int(value)

    feedback_text: str = arguments["feedback"]
    evidence = {key: feedback_text for key in dimensions}

    service = ToeflService(session)
    score = await service.record_writing_evaluation(
        user_id=owner_user_id,
        attempt_id=arguments["attempt_id"],
        question_ref="writing-1",
        dimensions=dimensions,
        evidence=evidence,
        flow_version=arguments["evaluator_flow_version"],
    )
    return {"score_id": score.id, "attempt_state": "evaluated"}


async def podcast_get_source_context_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    podcast_id: str = arguments["podcast_id"]
    source_version_id: str = arguments["source_version_id"]

    # Podcast + source version harus milik owner dan tidak tombstoned.
    source = (
        await session.execute(
            select(PodcastSourceVersion)
            .join(
                KnowledgeDocument,
                KnowledgeDocument.source_id == PodcastSourceVersion.id,
            )
            .where(
                PodcastSourceVersion.id == source_version_id,
                PodcastSourceVersion.podcast_id == podcast_id,
            )
        )
    ).scalars().first()
    if source is None:
        raise NotFoundError("Source version podcast tidak ditemukan.")

    document = (
        await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.podcast_id == podcast_id,
                KnowledgeDocument.owner_user_id == owner_user_id,
                KnowledgeDocument.source_id == source_version_id,
                KnowledgeDocument.publication_state == "published",
                KnowledgeDocument.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Dokumen sumber tidak published atau sudah dihapus.")

    stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    if "chunk_ids" in arguments:
        stmt = stmt.where(KnowledgeChunk.id.in_(arguments["chunk_ids"]))
    else:
        # Query sederhana berbasis substring pada chunk canonical; tanpa
        # arbitrary vector/filter bebas sesuai kontrak tool.
        needle = f"%{arguments['query'][:200]}%"
        stmt = stmt.where(KnowledgeChunk.text.ilike(needle))
    chunks = (
        await session.execute(stmt.order_by(KnowledgeChunk.position.asc()).limit(25))
    ).scalars().all()

    if "chunk_ids" in arguments:
        found = {chunk.id for chunk in chunks}
        missing = set(arguments["chunk_ids"]) - found
        if missing:
            raise NotFoundError("Sebagian chunk di luar scope source version ini.")

    return {
        "chunks": [
            {
                "chunk_id": chunk.id,
                "source_version_id": source_version_id,
                "text": chunk.text or "",
                "page": chunk.page_ref or 1,
            }
            for chunk in chunks
        ]
    }


def register_ingestion_tools(service: ToolExecutionService) -> None:
    service.register_handler(
        "conversation.persist_extraction", conversation_persist_extraction_handler
    )
    service.register_handler("toefl.record_evaluation", toefl_record_evaluation_handler)
    service.register_handler("podcast.get_source_context", podcast_get_source_context_handler)
