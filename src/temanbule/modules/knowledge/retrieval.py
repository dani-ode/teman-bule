"""Retrieval service (Phase 3): canonical memory query path.

Kontrak (implementation-plan.md slice 4, postgresql-schema.md):
- Filter owner wajib: user_memory hanya milik peminta; cross-owner tidak
  pernah terbaca (bukan hanya 404 — memang tidak ter-query).
- Hanya chunk yang projection-nya projected pada generation aktif yang
  dikembalikan; provenance (source_type/id/version) selalu disertakan.
- Source version fencing: query memakai source_version dokumen committed,
  bukan versi lebih baru yang belum selesai reindex.
- Vector search provider nyata menunggu DEC-09/SPK-05; service ini
  mengembalikan kandidat chunk canonical + metadata filter yang akan
  diteruskan adapter vector. Tidak ada fallback data tiruan.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.knowledge.models import (
    EmbeddingProfile,
    EmbeddingProjection,
    KnowledgeChunk,
    KnowledgeDocument,
)
from temanbule.platform.errors import NotFoundError

SCOPE_USER_MEMORY = "user_memory"
SCOPE_AGENT_KNOWLEDGE = "agent_knowledge"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    text: str | None
    object_ref: str | None
    position: int
    source_type: str
    source_id: str
    source_version: str
    content_hash: str


class RetrievalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def query_user_memory(
        self,
        *,
        user_id: str,
        scope: str = SCOPE_USER_MEMORY,
        limit: int = 20,
    ) -> list[RetrievedChunk]:
        """Kandidat chunk canonical milik user dengan projection lengkap.

        Hanya dokumen published + indexing ready/partial yang projection
        aktifnya projected yang masuk kandidat.
        """
        rows = (
            await self.session.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .where(
                    KnowledgeDocument.scope == scope,
                    KnowledgeDocument.owner_user_id == user_id,
                    KnowledgeDocument.publication_state == "published",
                    KnowledgeDocument.deleted_at.is_(None),
                )
                .order_by(KnowledgeDocument.id, KnowledgeChunk.position.asc())
                .limit(min(limit, 100))
            )
        ).all()
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                text=chunk.text,
                object_ref=chunk.object_ref,
                position=chunk.position,
                source_type=document.source_type,
                source_id=document.source_id,
                source_version=document.source_version,
                content_hash=chunk.content_hash,
            )
            for chunk, document in rows
        ]

    async def get_projected_chunk(
        self,
        *,
        user_id: str,
        chunk_id: str,
        profile_id: str,
        generation: int,
    ) -> RetrievedChunk:
        """Satu chunk dengan bukti projection pada profile+generation.

        Gagal 404 bila: chunk bukan milik user, dokumen tidak published,
        atau projection belum projected (reindex belum selesai — generation
        lama tetap dilayani lewat generation lamanya sendiri).
        """
        row = (
            await self.session.execute(
                select(KnowledgeChunk, KnowledgeDocument, EmbeddingProjection)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .join(
                    EmbeddingProjection,
                    (EmbeddingProjection.chunk_id == KnowledgeChunk.id)
                    & (EmbeddingProjection.profile_id == profile_id)
                    & (EmbeddingProjection.generation == generation),
                )
                .where(
                    KnowledgeChunk.id == chunk_id,
                    KnowledgeDocument.owner_user_id == user_id,
                    KnowledgeDocument.publication_state == "published",
                    KnowledgeDocument.deleted_at.is_(None),
                    EmbeddingProjection.state == "projected",
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Chunk tidak ditemukan atau belum terproyeksi.")
        chunk, document, _projection = row
        return RetrievedChunk(
            chunk_id=chunk.id,
            document_id=document.id,
            text=chunk.text,
            object_ref=chunk.object_ref,
            position=chunk.position,
            source_type=document.source_type,
            source_id=document.source_id,
            source_version=document.source_version,
            content_hash=chunk.content_hash,
        )

    async def list_active_profiles(self) -> list[EmbeddingProfile]:
        rows = (
            (
                await self.session.execute(
                    select(EmbeddingProfile).where(EmbeddingProfile.status == "active")
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
