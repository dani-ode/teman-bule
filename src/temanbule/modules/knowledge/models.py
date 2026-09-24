"""Knowledge module models (Phase 3): canonical documents/chunks + embeddings.

Canonical source/chunk harus mendukung rebuild penuh kedua embedding.
user_memory wajib owner; indexing_state terpisah dari publication_state.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base, TimestampMixin, utcnow


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(26))
    source_version: Mapped[str] = mapped_column(String(40))
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT")
    )
    podcast_id: Mapped[str | None] = mapped_column(String(26))
    publication_state: Mapped[str] = mapped_column(String(20), default="draft")
    indexing_state: Mapped[str] = mapped_column(String(20), default="pending")
    canonical_object_ref: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "source_version", "position",
            name="uq_knowledge_chunks_position",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    object_ref: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    page_ref: Mapped[int | None] = mapped_column(Integer)
    section_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmbeddingProfile(Base):
    __tablename__ = "embedding_profiles"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "model_id", "model_revision", "generation",
            name="uq_embedding_profiles_generation",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_catalog.id", ondelete="RESTRICT")
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_model_configurations.id", ondelete="RESTRICT")
    )
    model_revision: Mapped[int] = mapped_column(Integer)
    dimension: Mapped[int] = mapped_column(Integer)
    document_task_type: Mapped[str] = mapped_column(String(40))
    query_task_type: Mapped[str] = mapped_column(String(40))
    normalization: Mapped[str] = mapped_column(String(40))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="staged")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VectorCollectionRegistry(Base, TimestampMixin):
    __tablename__ = "vector_collection_registry"
    __table_args__ = (
        UniqueConstraint("environment", "scope", "profile_id", name="uq_vector_registry_binding"),
        UniqueConstraint("environment", "physical_name", name="uq_vector_registry_physical"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    environment: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(40))
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("embedding_profiles.id", ondelete="RESTRICT")
    )
    physical_name: Mapped[str] = mapped_column(String(128))
    similarity_metric: Mapped[str] = mapped_column(String(20))
    metadata_index_policy: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="staged")


class EmbeddingProjection(Base, TimestampMixin):
    __tablename__ = "embedding_projections"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "source_version", "profile_id", "generation",
            name="uq_embedding_projections_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[str] = mapped_column(String(40))
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("embedding_profiles.id", ondelete="RESTRICT")
    )
    generation: Mapped[int] = mapped_column(Integer)
    vector_id: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
