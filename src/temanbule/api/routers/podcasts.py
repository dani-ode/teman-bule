"""Podcasts router (Phase 7) + account deletion (Phase 8)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.identity.deletion import DeletionService
from temanbule.modules.podcasts.services import PodcastService

router = APIRouter(prefix="/v1", tags=["podcasts"])


class CreatePodcastRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class PodcastResponse(BaseModel):
    podcast_id: str
    title: str
    state: str


class AddSourceRequest(BaseModel):
    media_id: str = Field(min_length=1, max_length=26)


class SourceResponse(BaseModel):
    source_version_id: str
    revision: int
    parse_status: str


@router.post("/podcasts", response_model=PodcastResponse, status_code=201)
async def create_podcast(
    body: CreatePodcastRequest, current_user: CurrentUser, session: SessionDep
) -> PodcastResponse:
    service = PodcastService(session)
    podcast = await service.create_podcast(user_id=current_user.id, title=body.title)
    await session.commit()
    return PodcastResponse(podcast_id=podcast.id, title=podcast.title, state=podcast.state)


@router.post("/podcasts/{podcast_id}/sources", response_model=SourceResponse, status_code=201)
async def add_source(
    podcast_id: str,
    body: AddSourceRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> SourceResponse:
    service = PodcastService(session)
    source = await service.add_source(
        user_id=current_user.id, podcast_id=podcast_id, media_id=body.media_id
    )
    await session.commit()
    return SourceResponse(
        source_version_id=source.id, revision=source.revision, parse_status=source.parse_status
    )


class DeletionResponse(BaseModel):
    deletion_request_id: str
    status: str


@router.delete("/me", response_model=DeletionResponse, status_code=202)
async def delete_account(current_user: CurrentUser, session: SessionDep) -> DeletionResponse:
    """Mulai deletion job; 202 diterima, hasil tidak diklaim pada acceptance."""
    service = DeletionService(session)
    request = await service.request_deletion(user_id=current_user.id)
    await session.commit()
    return DeletionResponse(deletion_request_id=request.id, status=request.status)
