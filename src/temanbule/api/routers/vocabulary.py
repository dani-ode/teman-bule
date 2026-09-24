"""Vocabulary router (Phase 3): CRUD owner-scoped + reviews."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.vocabulary.models import VocabularyEntry
from temanbule.modules.vocabulary.services import VocabularyService

router = APIRouter(prefix="/v1/vocabulary", tags=["vocabulary"])


class SaveEntryRequest(BaseModel):
    lemma: str = Field(min_length=1, max_length=255)
    language: str = Field(min_length=2, max_length=10)
    definition: str | None = Field(default=None, max_length=2000)
    example: str | None = Field(default=None, max_length=2000)


class EntryResponse(BaseModel):
    entry_id: str
    lemma: str
    normalized_lemma: str
    language: str
    definition: str | None
    example: str | None
    state: str
    mastery_score: int


class UpdateStatusRequest(BaseModel):
    target_state: str = Field(pattern="^(new|learning|review|mastered|archived)$")


class ReviewRequest(BaseModel):
    result: str = Field(pattern="^(again|hard|good|easy)$")


def _entry_response(entry: VocabularyEntry) -> EntryResponse:
    return EntryResponse(
        entry_id=entry.id,
        lemma=entry.lemma,
        normalized_lemma=entry.normalized_lemma,
        language=entry.language,
        definition=entry.definition,
        example=entry.example,
        state=entry.state,
        mastery_score=entry.mastery_score,
    )


@router.post("", response_model=EntryResponse, status_code=201)
async def save_entry(
    body: SaveEntryRequest, current_user: CurrentUser, session: SessionDep
) -> EntryResponse:
    service = VocabularyService(session)
    entry, _created = await service.save_entry(
        user_id=current_user.id,
        lemma=body.lemma,
        language=body.language,
        definition=body.definition,
        example=body.example,
    )
    await session.commit()
    return _entry_response(entry)


@router.get("", response_model=list[EntryResponse])
async def list_entries(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EntryResponse]:
    service = VocabularyService(session)
    entries = await service.list_entries(user_id=current_user.id, limit=limit)
    return [_entry_response(e) for e in entries]


@router.get("/{entry_id}", response_model=EntryResponse)
async def get_entry(
    entry_id: str, current_user: CurrentUser, session: SessionDep
) -> EntryResponse:
    service = VocabularyService(session)
    entry = await service.get_entry(user_id=current_user.id, entry_id=entry_id)
    return _entry_response(entry)


@router.patch("/{entry_id}", response_model=EntryResponse)
async def update_status(
    entry_id: str,
    body: UpdateStatusRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> EntryResponse:
    service = VocabularyService(session)
    entry = await service.update_status(
        user_id=current_user.id, entry_id=entry_id, target_state=body.target_state
    )
    await session.commit()
    return _entry_response(entry)


@router.post("/{entry_id}/reviews", status_code=201)
async def record_review(
    entry_id: str,
    body: ReviewRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict[str, str]:
    service = VocabularyService(session)
    review = await service.record_review(
        user_id=current_user.id, entry_id=entry_id, result=body.result
    )
    await session.commit()
    return {
        "review_id": review.id,
        "previous_state": review.previous_state,
        "new_state": review.new_state,
    }
