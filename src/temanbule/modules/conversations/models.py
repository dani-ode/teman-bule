"""Conversations module models (Phase 3): sessions, messages, extractions.

Messages dan extractions append-only (trigger DB). Shared session kind
chat/call/podcast menghindari polymorphic FK. Sesuai postgresql-schema.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base, TimestampMixin, utcnow


class PracticeCategory(Base, TimestampMixin):
    __tablename__ = "practice_categories"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ConversationSession(Base, TimestampMixin):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        CheckConstraint("kind IN ('chat','call','podcast')", name="ck_conversation_sessions_kind"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="active", index=True)
    runtime_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_snapshots.id", ondelete="RESTRICT")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeSession(Base):
    __tablename__ = "practice_sessions"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("practice_categories.id", ondelete="RESTRICT"), index=True
    )
    agent_version_id: Mapped[str] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )


class ConversationMessage(Base):
    """Append-only; UNIQUE(session,sequence) dan UNIQUE(session,client_key)."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_conversation_messages_sequence"),
        UniqueConstraint("session_id", "client_key", name="uq_conversation_messages_client_key"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | agent | system
    agent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    modality: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(26))
    sequence: Mapped[int] = mapped_column(Integer)
    client_key: Mapped[str | None] = mapped_column(String(128))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_state: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationExtraction(Base):
    """Append-only canonical ingestion output; dedupe per range+schema+flow."""

    __tablename__ = "conversation_extractions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "source_start",
            "source_end",
            "schema_version",
            "flow_version",
            name="uq_conversation_extractions_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    source_start: Mapped[int] = mapped_column(Integer)
    source_end: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(40))
    flow_version: Mapped[str] = mapped_column(String(40))
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserFact(Base, TimestampMixin):
    __tablename__ = "user_facts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fact_key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    provenance_ref: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[str] = mapped_column(String(40))
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_facts.id", ondelete="SET NULL")
    )
    consent_scope: Mapped[str] = mapped_column(String(40), default="learning")


class LearningAssessment(Base):
    __tablename__ = "learning_assessments"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "evidence_start",
            "evidence_end",
            "rubric_version",
            "flow_version",
            name="uq_learning_assessments_evidence",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="RESTRICT")
    )
    evidence_start: Mapped[int] = mapped_column(Integer)
    evidence_end: Mapped[int] = mapped_column(Integer)
    rubric_version: Mapped[str] = mapped_column(String(40))
    dimensions: Mapped[str] = mapped_column(Text)
    suggested_level: Mapped[str | None] = mapped_column(String(20))
    flow_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
