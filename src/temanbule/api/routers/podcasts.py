"""Podcasts router (Phase 7) + account deletion (Phase 8)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.modules.identity.deletion import DeletionService
from temanbule.modules.podcasts.langflow_adapter import LangflowPodcastAdapter
from temanbule.modules.podcasts.models import Podcast, PodcastSourceVersion
from temanbule.modules.podcasts.services import PodcastService
from temanbule.platform.errors import FeatureUnavailableError

router = APIRouter(prefix="/v1", tags=["podcasts"])


class CreatePodcastRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class PodcastResponse(BaseModel):
    podcast_id: str
    title: str
    state: str
    created_at: str | None = None


class PodcastListItem(BaseModel):
    podcast_id: str
    title: str
    state: str
    created_at: str


class SegmentResponse(BaseModel):
    segment_id: str
    position: int
    agent_version_id: str
    text: str
    citations: list[str] | None
    estimated_ms: int | None


class PlaybackResponse(BaseModel):
    playback_id: str
    podcast_id: str
    script_version_id: str
    session_id: str
    state: str
    segment_cursor: int
    offset_ms: int
    elapsed_ms: int
    end_reason: str | None


class UpdatePodcastRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class PlayPodcastRequest(BaseModel):
    """Play = generate script baru (sync) + playback/session baru."""

    target_duration_seconds: int = Field(gt=0, le=7200)


class IngestionStatusResponse(BaseModel):
    podcast_id: str
    state: str
    source_version_id: str | None
    parse_status: str | None
    page_count: int | None


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
    """Attach PDF finalized; ingestion Langflow (background) terjadwal via outbox."""
    service = PodcastService(session)
    source = await service.add_source(
        user_id=current_user.id, podcast_id=podcast_id, media_id=body.media_id
    )
    await session.commit()
    return SourceResponse(
        source_version_id=source.id, revision=source.revision, parse_status=source.parse_status
    )


@router.get("/podcasts/{podcast_id}/ingestion-status", response_model=IngestionStatusResponse)
async def get_ingestion_status(
    podcast_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> IngestionStatusResponse:
    """Status ingestion dokumen terkini; dipoll frontend sampai parsed/failed."""
    service = PodcastService(session)
    podcast = await service.get_podcast(user_id=current_user.id, podcast_id=podcast_id)
    source: PodcastSourceVersion | None = None
    if podcast.current_source_version_id is not None:
        source = (
            await session.execute(
                select(PodcastSourceVersion).where(
                    PodcastSourceVersion.id == podcast.current_source_version_id
                )
            )
        ).scalar_one_or_none()
    return IngestionStatusResponse(
        podcast_id=podcast.id,
        state=podcast.state,
        source_version_id=source.id if source is not None else None,
        parse_status=source.parse_status if source is not None else None,
        page_count=source.page_count if source is not None else None,
    )


@router.get("/podcasts", response_model=list[PodcastListItem])
async def list_podcasts(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PodcastListItem]:
    rows = (
        (
            await session.execute(
                select(Podcast)
                .where(Podcast.user_id == current_user.id)
                .order_by(Podcast.created_at.desc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        PodcastListItem(
            podcast_id=p.id,
            title=p.title,
            state=p.state,
            created_at=p.created_at.isoformat(),
        )
        for p in rows
    ]


@router.get("/podcasts/{podcast_id}", response_model=PodcastResponse)
async def get_podcast(
    podcast_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> PodcastResponse:
    service = PodcastService(session)
    podcast = await service.get_podcast(user_id=current_user.id, podcast_id=podcast_id)
    return PodcastResponse(
        podcast_id=podcast.id,
        title=podcast.title,
        state=podcast.state,
        created_at=podcast.created_at.isoformat(),
    )


@router.patch("/podcasts/{podcast_id}", response_model=PodcastResponse)
async def update_podcast(
    podcast_id: str,
    body: UpdatePodcastRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> PodcastResponse:
    service = PodcastService(session)
    podcast = await service.update_title(
        user_id=current_user.id, podcast_id=podcast_id, title=body.title
    )
    await session.commit()
    return PodcastResponse(
        podcast_id=podcast.id,
        title=podcast.title,
        state=podcast.state,
        created_at=podcast.created_at.isoformat(),
    )


@router.delete("/podcasts/{podcast_id}", status_code=204)
async def delete_podcast(
    podcast_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    service = PodcastService(session)
    await service.delete_podcast(user_id=current_user.id, podcast_id=podcast_id)
    await session.commit()


@router.get("/podcasts/{podcast_id}/segments", response_model=list[SegmentResponse])
async def list_segments(
    podcast_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[SegmentResponse]:
    service = PodcastService(session)
    segments = await service.list_segments(
        user_id=current_user.id, podcast_id=podcast_id
    )
    return [
        SegmentResponse(
            segment_id=s.id,
            position=s.position,
            agent_version_id=s.agent_version_id,
            text=s.text,
            citations=json.loads(s.citations) if s.citations else None,
            estimated_ms=s.estimated_ms,
        )
        for s in segments
    ]


@router.post("/podcasts/{podcast_id}/playbacks", response_model=PlaybackResponse, status_code=201)
async def create_playback(
    podcast_id: str,
    body: PlayPodcastRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> PlaybackResponse:
    """Play podcast: generate script baru via Langflow (sync) + playback baru.

    Script tidak dipakai ulang lintas klik play — satu script version selalu
    satu conversation session. Hasil Langflow divalidasi dua speaker
    Elean/Willy + citations sebelum disimpan immutable.
    """
    if not settings.feature_podcast_enabled:
        raise FeatureUnavailableError("Fitur podcast belum aktif.")
    if not settings.langflow_api_key:
        raise FeatureUnavailableError("Langflow belum dikonfigurasi.")
    service = PodcastService(session, settings)
    _script, playback = await service.generate_script_and_start_playback(
        user_id=current_user.id,
        podcast_id=podcast_id,
        target_duration_seconds=body.target_duration_seconds,
        lease_owner=f"user-{current_user.id}",
        deadline_seconds=3600,
        adapter=LangflowPodcastAdapter(settings),
    )
    await session.commit()
    return PlaybackResponse(
        playback_id=playback.id,
        podcast_id=playback.podcast_id,
        script_version_id=playback.script_version_id,
        session_id=playback.session_id,
        state=playback.state,
        segment_cursor=playback.segment_cursor,
        offset_ms=playback.offset_ms,
        elapsed_ms=playback.elapsed_ms,
        end_reason=playback.end_reason,
    )


@router.get("/podcasts/{podcast_id}/playbacks/{playback_id}", response_model=PlaybackResponse)
async def get_playback(
    podcast_id: str,
    playback_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> PlaybackResponse:
    service = PodcastService(session)
    playback = await service.get_playback(
        user_id=current_user.id, podcast_id=podcast_id, playback_id=playback_id
    )
    return PlaybackResponse(
        playback_id=playback.id,
        podcast_id=playback.podcast_id,
        script_version_id=playback.script_version_id,
        session_id=playback.session_id,
        state=playback.state,
        segment_cursor=playback.segment_cursor,
        offset_ms=playback.offset_ms,
        elapsed_ms=playback.elapsed_ms,
        end_reason=playback.end_reason,
    )


@router.post("/podcasts/{podcast_id}/playbacks/{playback_id}:end", response_model=PlaybackResponse)
async def end_playback(
    podcast_id: str,
    playback_id: str,
    current_user: CurrentUser,
    session: SessionDep,
) -> PlaybackResponse:
    service = PodcastService(session)
    playback = await service.end_playback(
        user_id=current_user.id, playback_id=playback_id, end_reason="user_hangup"
    )
    await session.commit()
    return PlaybackResponse(
        playback_id=playback.id,
        podcast_id=playback.podcast_id,
        script_version_id=playback.script_version_id,
        session_id=playback.session_id,
        state=playback.state,
        segment_cursor=playback.segment_cursor,
        offset_ms=playback.offset_ms,
        elapsed_ms=playback.elapsed_ms,
        end_reason=playback.end_reason,
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
