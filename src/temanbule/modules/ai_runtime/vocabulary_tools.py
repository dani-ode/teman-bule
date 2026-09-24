"""Wiring handler tool vocabulary ke ToolExecutionService (Phase 3).

Handler menerjemahkan argumen tool CallCraft → domain service call.
actor_user_id TIDAK diterima dari arguments; owner dari grant.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.vocabulary.services import VocabularyService
from temanbule.platform.errors import ValidationError


async def vocabulary_save_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    lemma = arguments.get("lemma")
    language = arguments.get("language")
    if not isinstance(lemma, str) or not isinstance(language, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "lemma/language", "message": "wajib string"}],
        )
    service = VocabularyService(session)
    entry, created = await service.save_entry(
        user_id=owner_user_id,
        lemma=lemma,
        language=language,
        definition=_optional_str(arguments, "definition"),
        example=_optional_str(arguments, "example"),
        provenance=_optional_str(arguments, "source_message_id"),
    )
    return {
        "resource_id": entry.id,
        "resource_version": 1,
        "created": created,
        "state": entry.state,
    }


async def vocabulary_update_status_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    entry_id = arguments.get("entry_id")
    target_state = arguments.get("target_state")
    if not isinstance(entry_id, str) or not isinstance(target_state, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "entry_id/target_state", "message": "wajib string"}],
        )
    service = VocabularyService(session)
    entry = await service.update_status(
        user_id=owner_user_id,
        entry_id=entry_id,
        target_state=target_state,
    )
    return {"resource_id": entry.id, "resource_version": 1, "state": entry.state}


async def vocabulary_get_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    entry_id = arguments.get("entry_id")
    if not isinstance(entry_id, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "entry_id", "message": "wajib string"}],
        )
    service = VocabularyService(session)
    entry = await service.get_entry(user_id=owner_user_id, entry_id=entry_id)
    return {
        "resource_id": entry.id,
        "lemma": entry.lemma,
        "language": entry.language,
        "state": entry.state,
        "definition": entry.definition,
        "example": entry.example,
    }


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": key, "message": "harus string"}],
        )
    return value


def register_vocabulary_tools(service: ToolExecutionService) -> None:
    service.register_handler("vocabulary.save", vocabulary_save_handler)
    service.register_handler("vocabulary.update_status", vocabulary_update_status_handler)
    service.register_handler("vocabulary.get", vocabulary_get_handler)
