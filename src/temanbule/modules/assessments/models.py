"""TOEFL module models (Phase 4). Scores append-only (trigger DB)."""

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


class ToeflTestVersion(Base):
    __tablename__ = "toefl_test_versions"
    __table_args__ = (UniqueConstraint("code", "revision", name="uq_toefl_test_revision"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    rubric_version: Mapped[str] = mapped_column(String(40))
    definition: Mapped[str] = mapped_column(Text)
    publication_state: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToeflAttempt(Base, TimestampMixin):
    __tablename__ = "toefl_attempts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    test_version_id: Mapped[str] = mapped_column(
        ForeignKey("toefl_test_versions.id", ondelete="RESTRICT")
    )
    runtime_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_snapshots.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(20), default="created")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToeflSubmission(Base, TimestampMixin):
    __tablename__ = "toefl_submissions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_ref", name="uq_toefl_submissions_question"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("toefl_attempts.id", ondelete="CASCADE"), index=True
    )
    question_ref: Mapped[str] = mapped_column(String(80))
    section: Mapped[str] = mapped_column(String(20))
    answer: Mapped[str | None] = mapped_column(Text)
    media_refs: Mapped[str | None] = mapped_column(Text)


class ToeflScore(Base):
    """Append-only; immutable setelah tercatat."""

    __tablename__ = "toefl_scores"
    __table_args__ = (
        UniqueConstraint("attempt_id", "rubric_version", name="uq_toefl_scores_rubric"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("toefl_attempts.id", ondelete="RESTRICT"), index=True
    )
    rubric_version: Mapped[str] = mapped_column(String(40))
    objective_dimensions: Mapped[str] = mapped_column(Text)
    subjective_dimensions: Mapped[str | None] = mapped_column(Text)
    total_score: Mapped[int] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(Text)
    flow_version: Mapped[str | None] = mapped_column(String(40))
    review_status: Mapped[str] = mapped_column(String(20), default="final")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
