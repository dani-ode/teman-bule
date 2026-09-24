"""Learning service (Phase 4): published content read + progress tracking.

Kontrak: hanya content published yang bisa dipelajari/di-progress;
progress bounded 0..100; owner-scoped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.learning.models import (
    Course,
    LearningContentVersion,
    LearningProgress,
    Lesson,
)
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid


class LearningService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_published_courses(self, limit: int = 50) -> list[Course]:
        rows = (
            (
                await self.session.execute(
                    select(Course)
                    .where(Course.status == "published")
                    .order_by(Course.title.asc())
                    .limit(min(limit, 100))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def get_published_lesson_content(self, lesson_id: str) -> LearningContentVersion:
        lesson = (
            await self.session.execute(
                select(Lesson).where(Lesson.id == lesson_id, Lesson.status == "published")
            )
        ).scalar_one_or_none()
        if lesson is None:
            raise NotFoundError("Lesson tidak ditemukan.")
        content = (
            await self.session.execute(
                select(LearningContentVersion)
                .where(
                    LearningContentVersion.lesson_id == lesson_id,
                    LearningContentVersion.publication_state == "published",
                )
                .order_by(LearningContentVersion.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if content is None:
            raise NotFoundError("Konten lesson belum dipublish.")
        return content

    async def record_progress(
        self,
        *,
        user_id: str,
        content_version_id: str,
        status: str,
        completion_percent: int,
    ) -> LearningProgress:
        """Upsert progress; published content wajib; percent bounded."""
        if status not in ("started", "completed"):
            raise ValidationError(
                "Status progress tidak valid.",
                details=[{"field": "status", "message": status}],
            )
        if not 0 <= completion_percent <= 100:
            raise ValidationError(
                "completion_percent di luar batas.",
                details=[{"field": "completion_percent", "message": "harus 0..100"}],
            )
        content = (
            await self.session.execute(
                select(LearningContentVersion).where(
                    LearningContentVersion.id == content_version_id,
                    LearningContentVersion.publication_state == "published",
                )
            )
        ).scalar_one_or_none()
        if content is None:
            raise NotFoundError("Content version tidak ditemukan atau belum published.")

        existing = (
            await self.session.execute(
                select(LearningProgress).where(
                    LearningProgress.user_id == user_id,
                    LearningProgress.content_version_id == content_version_id,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None:
            # Progress tidak boleh mundur diam-diam
            if completion_percent < existing.completion_percent:
                raise ConflictError(
                    "completion_percent tidak boleh mundur.",
                    code="PROGRESS_REGRESSION",
                )
            existing.status = status
            existing.completion_percent = completion_percent
            existing.last_activity_at = now
            await self.session.flush()
            return existing

        progress = LearningProgress(
            id=new_ulid(),
            user_id=user_id,
            content_version_id=content_version_id,
            status=status,
            completion_percent=completion_percent,
            last_activity_at=now,
        )
        self.session.add(progress)
        await self.session.flush()
        return progress

    async def get_user_progress(self, *, user_id: str, limit: int = 50) -> list[LearningProgress]:
        rows = (
            (
                await self.session.execute(
                    select(LearningProgress)
                    .where(LearningProgress.user_id == user_id)
                    .order_by(LearningProgress.last_activity_at.desc())
                    .limit(min(limit, 100))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
