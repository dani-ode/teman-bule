"""Ingestion service (Phase 3): canonical extraction + dual embedding dispatch.

Kontrak (langflow-flows.md, api-events.md, postgresql-schema.md):
- SQL job pemilik retry/completion; processor AI (Langflow) hanya mengolah
  data turunan lewat ExtractionPort. Pesan asli sudah tersimpan backend.
- Extraction canonical idempoten per (session, range, schema, flow_version)
  — UNIQUE constraint DB + cek-tulis dalam satu transaksi.
- Dual embedding dispatch adalah fan-out DETERMINISTIK via runtime API:
  satu job terpisah per profile aktif; satu branch gagal dapat diulang
  tanpa mengulang branch yang sukses (dedupe_key per chunk+profile+gen).
- Tanpa fake success: processor gagal → job tetap nonterminal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.models import (
    ConversationExtraction,
    ConversationMessage,
    ConversationSession,
)
from temanbule.modules.knowledge.models import (
    EmbeddingProfile,
    EmbeddingProjection,
    KnowledgeChunk,
    KnowledgeDocument,
)
from temanbule.modules.reliability.models import BackgroundJob
from temanbule.modules.reliability.outbox import record_outbox_event
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid, sha256_hex

JOB_PURPOSE_INGESTION = "conversation_ingestion"
JOB_PURPOSE_EMBEDDING = "embedding_projection"

SCHEMA_VERSION_EXTRACTION = "extraction.v1"


class ExtractionPort(Protocol):
    """Port ke flow conversation_ingestion (Langflow). Konkret menunggu DEC-10.

    Input: rentang pesan tersimpan. Output: data canonical terstruktur
    (summary/evidence) sesuai schema_version. TIDAK menyimpan ke DB —
    persistence hanya lewat service ini.
    """

    async def extract(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        schema_version: str,
        flow_version: str,
    ) -> dict[str, Any]: ...


class EmbeddingPort(Protocol):
    """Port ke flow embedding_projection_{gemini,openai}. Konkret menunggu DEC-09.

    Mengembalikan vector_id provider untuk satu chunk pada satu profile.
    """

    async def embed(
        self, *, chunk_text: str, profile: EmbeddingProfile
    ) -> str: ...


class IngestionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_ingestion(
        self,
        *,
        job_id: str,
        session_id: str,
        owner_user_id: str,
        source_start: int,
        source_end: int,
        flow_version: str,
        extractor: ExtractionPort,
    ) -> ConversationExtraction:
        """Proses rentang pesan → canonical extraction (idempoten).

        Replay job dengan parameter sama mengembalikan extraction existing
        tanpa memanggil processor ulang.
        """
        if source_end < source_start:
            raise ValidationError("Range tidak valid.")

        existing = (
            await self.session.execute(
                select(ConversationExtraction).where(
                    ConversationExtraction.session_id == session_id,
                    ConversationExtraction.source_start == source_start,
                    ConversationExtraction.source_end == source_end,
                    ConversationExtraction.schema_version == SCHEMA_VERSION_EXTRACTION,
                    ConversationExtraction.flow_version == flow_version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        conversation = (
            await self.session.execute(
                select(ConversationSession).where(ConversationSession.id == session_id)
            )
        ).scalar_one_or_none()
        if conversation is None or conversation.user_id != owner_user_id:
            raise NotFoundError("Session tidak ditemukan.")

        messages = (
            (
                await self.session.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.session_id == session_id,
                        ConversationMessage.sequence >= source_start,
                        ConversationMessage.sequence <= source_end,
                    )
                    .order_by(ConversationMessage.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        if not messages:
            raise ValidationError("Rentang pesan kosong.")

        payload = [
            {
                "sequence": m.sequence,
                "role": m.role,
                "modality": m.modality,
                "text": m.text,
            }
            for m in messages
        ]
        data = await extractor.extract(
            session_id=session_id,
            messages=payload,
            schema_version=SCHEMA_VERSION_EXTRACTION,
            flow_version=flow_version,
        )

        extraction = ConversationExtraction(
            id=new_ulid(),
            session_id=session_id,
            owner_user_id=owner_user_id,
            source_start=source_start,
            source_end=source_end,
            schema_version=SCHEMA_VERSION_EXTRACTION,
            flow_version=flow_version,
            data=json.dumps(data, sort_keys=True),
        )
        self.session.add(extraction)
        await self.session.flush()

        # Outbox atomik: facts & assessment jobs independen mengikuti event ini.
        record_outbox_event(
            self.session,
            aggregate_type="conversation_session",
            aggregate_id=session_id,
            aggregate_version=await self._next_aggregate_version(session_id),
            event_type="conversation.extraction_committed.v1",
            payload={
                "extraction_id": extraction.id,
                "session_id": session_id,
                "owner_user_id": owner_user_id,
                "source_start": source_start,
                "source_end": source_end,
                "flow_version": flow_version,
            },
        )
        await self.session.flush()
        return extraction

    async def _next_aggregate_version(self, session_id: str) -> int:
        from temanbule.modules.reliability.models import OutboxEvent

        current = (
            await self.session.execute(
                select(func.coalesce(func.max(OutboxEvent.aggregate_version), 0)).where(
                    OutboxEvent.aggregate_type == "conversation_session",
                    OutboxEvent.aggregate_id == session_id,
                )
            )
        ).scalar_one()
        return current + 1


class KnowledgeService:
    """Canonical documents/chunks + dual projection fan-out."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit_canonical_document(
        self,
        *,
        scope: str,
        source_type: str,
        source_id: str,
        source_version: str,
        content_hash: str,
        chunks: list[str],
        owner_user_id: str | None,
    ) -> tuple[KnowledgeDocument, bool]:
        """Commit dokumen + chunks canonical (idempoten per source+version).

        Mengembalikan (document, created). Replay mengembalikan existing.
        """
        if scope == "user_memory" and owner_user_id is None:
            raise ValidationError("user_memory wajib owner.")
        existing = (
            await self.session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.scope == scope,
                    KnowledgeDocument.source_type == source_type,
                    KnowledgeDocument.source_id == source_id,
                    KnowledgeDocument.source_version == source_version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        document = KnowledgeDocument(
            id=new_ulid(),
            scope=scope,
            owner_user_id=owner_user_id,
            source_type=source_type,
            source_id=source_id,
            source_version=source_version,
            publication_state="published",
            indexing_state="pending",
            content_hash=content_hash,
        )
        self.session.add(document)
        await self.session.flush()
        for position, text in enumerate(chunks):
            self.session.add(
                KnowledgeChunk(
                    id=new_ulid(),
                    document_id=document.id,
                    source_version=source_version,
                    position=position,
                    text=text,
                    content_hash=sha256_hex(text),
                )
            )
        await self.session.flush()
        return document, True

    async def dispatch_dual_embedding(
        self, *, document_id: str
    ) -> list[BackgroundJob]:
        """Buat SATU job per (chunk, profile aktif, generation) — deterministik.

        Job yang sudah ada (dedupe_key sama) tidak dibuat ulang: branch sukses
        tidak diulang saat branch lain gagal.
        """
        document = (
            await self.session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
            )
        ).scalar_one_or_none()
        if document is None:
            raise NotFoundError("Dokumen tidak ditemukan.")

        profiles = (
            (
                await self.session.execute(
                    select(EmbeddingProfile).where(EmbeddingProfile.status == "active")
                )
            )
            .scalars()
            .all()
        )
        if not profiles:
            raise ConflictError(
                "Tidak ada embedding profile aktif.",
                code="EMBEDDING_PROFILE_MISSING",
            )

        chunks = (
            (
                await self.session.execute(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document.id)
                    .order_by(KnowledgeChunk.position.asc())
                )
            )
            .scalars()
            .all()
        )

        jobs: list[BackgroundJob] = []
        now = datetime.now(UTC)
        for chunk in chunks:
            for profile in profiles:
                dedupe_key = f"embed:{chunk.id}:{profile.id}:{profile.generation}"
                existing_job = (
                    await self.session.execute(
                        select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key)
                    )
                ).scalar_one_or_none()
                if existing_job is not None:
                    continue  # single-branch retry: branch sukses tidak diulang

                # Projection row sebagai state tracker per target
                projection = (
                    await self.session.execute(
                        select(EmbeddingProjection).where(
                            EmbeddingProjection.chunk_id == chunk.id,
                            EmbeddingProjection.source_version == chunk.source_version,
                            EmbeddingProjection.profile_id == profile.id,
                            EmbeddingProjection.generation == profile.generation,
                        )
                    )
                ).scalar_one_or_none()
                if projection is None:
                    projection = EmbeddingProjection(
                        id=new_ulid(),
                        chunk_id=chunk.id,
                        source_version=chunk.source_version,
                        profile_id=profile.id,
                        generation=profile.generation,
                        content_hash=chunk.content_hash,
                    )
                    self.session.add(projection)

                job = BackgroundJob(
                    id=new_ulid(),
                    dedupe_key=dedupe_key,
                    purpose=JOB_PURPOSE_EMBEDDING,
                    run_after=now,
                    payload=json.dumps(
                        {
                            "chunk_id": chunk.id,
                            "profile_id": profile.id,
                            "generation": profile.generation,
                            "document_id": document.id,
                            "scope": document.scope,
                            "owner_user_id": document.owner_user_id,
                        },
                        sort_keys=True,
                    ),
                )
                self.session.add(job)
                jobs.append(job)

        if document.indexing_state == "pending":
            document.indexing_state = "partial"
        await self.session.flush()
        return jobs

    async def run_embedding_job(
        self,
        *,
        job: BackgroundJob,
        embedder: EmbeddingPort,
    ) -> EmbeddingProjection:
        """Eksekusi satu branch embedding; idempoten per projection target."""
        payload = json.loads(job.payload)
        chunk_id = payload["chunk_id"]
        profile_id = payload["profile_id"]
        generation = payload["generation"]

        projection = (
            await self.session.execute(
                select(EmbeddingProjection).where(
                    EmbeddingProjection.chunk_id == chunk_id,
                    EmbeddingProjection.profile_id == profile_id,
                    EmbeddingProjection.generation == generation,
                )
            )
        ).scalar_one_or_none()
        if projection is None:
            raise NotFoundError("Projection target tidak ditemukan.")
        if projection.state == "projected":
            return projection  # branch ini sudah sukses; tidak diulang

        chunk = (
            await self.session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id)
            )
        ).scalar_one_or_none()
        profile = (
            await self.session.execute(
                select(EmbeddingProfile).where(EmbeddingProfile.id == profile_id)
            )
        ).scalar_one_or_none()
        if chunk is None or profile is None:
            raise NotFoundError("Chunk atau profile tidak ditemukan.")
        if chunk.text is None:
            raise ValidationError("Chunk tanpa text inline tidak dapat diembed.")

        vector_id = await embedder.embed(chunk_text=chunk.text, profile=profile)
        projection.vector_id = vector_id
        projection.state = "projected"
        await self.session.flush()
        return projection
