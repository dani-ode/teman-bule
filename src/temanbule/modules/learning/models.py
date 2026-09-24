"""Learning module models (Phase 4): courses, units, lessons, content, progress."""

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


class Course(Base, TimestampMixin):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="draft")


class CourseUnit(Base):
    __tablename__ = "course_units"
    __table_args__ = (UniqueConstraint("course_id", "position", name="uq_course_units_position"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lesson(Base, TimestampMixin):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint("unit_id", "position", name="uq_lessons_position"),
        UniqueConstraint("unit_id", "slug", name="uq_lessons_slug"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("course_units.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    level: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")


class LearningContentVersion(Base):
    """Published immutable; perubahan lewat revision baru."""

    __tablename__ = "learning_content_versions"
    __table_args__ = (
        UniqueConstraint("lesson_id", "revision", name="uq_learning_content_revision"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lessons.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    media_refs: Mapped[str | None] = mapped_column(Text)
    publication_state: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningProgress(Base, TimestampMixin):
    __tablename__ = "learning_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "content_version_id", name="uq_learning_progress_user_content"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("learning_content_versions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20), default="started")
    completion_percent: Mapped[int] = mapped_column(Integer, default=0)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
