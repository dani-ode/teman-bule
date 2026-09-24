"""Vocabulary module models (Phase 3). Reviews append-only."""

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


class VocabularyEntry(Base, TimestampMixin):
    __tablename__ = "vocabulary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_lemma", "language", name="uq_vocabulary_lemma"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    lemma: Mapped[str] = mapped_column(String(255))
    normalized_lemma: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(10))
    definition: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="new")
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mastery_score: Mapped[int] = mapped_column(Integer, default=0)


class VocabularyReview(Base):
    """Append-only review history."""

    __tablename__ = "vocabulary_reviews"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("vocabulary_entries.id", ondelete="CASCADE"), index=True
    )
    result: Mapped[str] = mapped_column(String(20))  # again|hard|good|easy
    previous_state: Mapped[str] = mapped_column(String(20))
    new_state: Mapped[str] = mapped_column(String(20))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
