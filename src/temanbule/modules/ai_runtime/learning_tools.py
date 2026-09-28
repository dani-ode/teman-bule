"""Wiring handler tool profile/learning/progress ke ToolExecutionService.

actor_user_id TIDAK diterima dari arguments; owner berasal dari grant
terverifikasi. Argumen sudah divalidasi ketat oleh tool_schemas sebelum handler
dipanggil.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.identity.models import UserProfile
from temanbule.modules.learning.models import LearningContentVersion, LearningProgress
from temanbule.modules.learning.services import LearningService
from temanbule.platform.errors import NotFoundError


async def profile_update_preferences_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Update preferensi learner yang allowlisted.

    Tidak dapat mengubah identity, role, credential, atau provider catalog.
    """
    profile = (
        await session.execute(
            select(UserProfile).where(UserProfile.user_id == owner_user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=owner_user_id)
        session.add(profile)

    if "english_level" in arguments:
        profile.english_level = arguments["english_level"]
    if "learning_goals" in arguments:
        profile.learning_goals = json.dumps(arguments["learning_goals"])
    if "tutoring_preferences" in arguments:
        # Kolom preferences menyimpan JSON preferensi tutoring yang allowlisted.
        existing: dict[str, Any] = {}
        if profile.preferences:
            try:
                loaded = json.loads(profile.preferences)
                if isinstance(loaded, dict):
                    existing = loaded
            except ValueError:
                existing = {}
        existing.update(arguments["tutoring_preferences"])
        profile.preferences = json.dumps(existing, sort_keys=True)

    await session.flush()
    # UserProfile tidak memiliki kolom version; updated_at menjadi referensi
    # perubahan yang dapat diaudit. profile_version dikembalikan sebagai
    # versi konvensi monotonik minimal sesuai output schema.
    return {"profile_version": 1}


async def learning_get_progress_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Baca progress untuk lesson yang dipilih eksplisit; owner-scoped."""
    lesson_ids: list[str] = []
    if "lesson_id" in arguments:
        lesson_ids = [arguments["lesson_id"]]
    else:
        lesson_ids = list(arguments["lesson_ids"])

    # Map lesson -> published content version -> progress milik owner.
    content_rows = (
        await session.execute(
            select(LearningContentVersion).where(
                LearningContentVersion.lesson_id.in_(lesson_ids),
                LearningContentVersion.publication_state == "published",
            )
        )
    ).scalars().all()
    content_by_lesson = {row.lesson_id: row for row in content_rows}

    progress_rows = (
        await session.execute(
            select(LearningProgress).where(
                LearningProgress.user_id == owner_user_id,
                LearningProgress.content_version_id.in_(
                    [row.id for row in content_rows] or ["__none__"]
                ),
            )
        )
    ).scalars().all()
    progress_by_content = {row.content_version_id: row for row in progress_rows}

    items: list[dict[str, Any]] = []
    for lesson_id in lesson_ids:
        content = content_by_lesson.get(lesson_id)
        if content is None:
            raise NotFoundError(f"Lesson '{lesson_id}' tidak ditemukan atau belum published.")
        progress = progress_by_content.get(content.id)
        items.append(
            {
                "lesson_id": lesson_id,
                "content_version_id": content.id,
                "status": progress.status if progress else "not_started",
                "completion_percent": progress.completion_percent if progress else 0,
            }
        )
    return {"items": items}


async def learning_record_progress_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Catat progress berversi. Default deny sudah ditegakkan oleh authorization
    matrix (tool ini tidak punya purpose), sehingga handler hanya berjalan bila
    policy allowlist eksplisit diaktifkan di masa depan.
    """
    service = LearningService(session)
    progress = await service.record_progress(
        user_id=owner_user_id,
        content_version_id=arguments["content_version_id"],
        status=arguments["status"],
        completion_percent=int(arguments["completion_percent"]),
    )
    return {
        "resource_id": progress.id,
        "resource_version": 1,
        "status": progress.status,
        "completion_percent": progress.completion_percent,
    }


def register_learning_tools(service: ToolExecutionService) -> None:
    service.register_handler("profile.update_preferences", profile_update_preferences_handler)
    service.register_handler("learning.get_progress", learning_get_progress_handler)
    service.register_handler("learning.record_progress", learning_record_progress_handler)
