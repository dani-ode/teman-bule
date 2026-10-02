"""Practice (chat) router (Phase 3): sessions + messages.

Kontrak api-events.md:
- POST /v1/practice/sessions agent_id/category_id → 201
- POST .../{id}/messages text + client_message_id → 201; replay idempoten
- GET .../{id} dan .../{id}/messages owner-scoped (404 cross-owner)
- POST .../{id}:complete → 200

Chat resolves the active PostgreSQL registry entry and calls Langflow.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.modules.conversations.chat import ChatService
from temanbule.modules.conversations.models import ConversationSession, PracticeCategory
from temanbule.modules.conversations.services import ConversationService
from temanbule.platform.settings import Settings

router = APIRouter(prefix="/v1/practice", tags=["practice"])


class CreateSessionRequest(BaseModel):
    agent_code: str = Field(pattern="^(elean|willy)$")
    category_id: str = Field(min_length=1, max_length=26)


class SessionResponse(BaseModel):
    session_id: str
    kind: str
    state: str
    started_at: str
    agent_code: str | None = None
    category_id: str | None = None


class FirstMessageResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    text: str
    modality: str
    # URL audio S3/MinIO dari workflow (TTS); null bila greeting text-only.
    audio_url: str | None
    audio_duration_ms: int | None
    sequence: int
    terminal_state: str
    created_at: str


class CreateSessionResponse(BaseModel):
    session_id: str
    kind: str
    state: str
    started_at: str
    agent_code: str
    category_id: str
    first_message: FirstMessageResponse | None


class SessionListItemResponse(BaseModel):
    session_id: str
    kind: str
    state: str
    category_id: str
    agent_code: str
    started_at: str
    ended_at: str | None


class CreateMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class CreateVoiceMessageRequest(BaseModel):
    media_id: str = Field(min_length=1, max_length=26)
    text: str | None = Field(default=None, max_length=8000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    sequence: int
    terminal_state: str
    created_at: str


def _session_response(s: ConversationSession) -> SessionResponse:
    return SessionResponse(
        session_id=s.id,
        kind=s.kind,
        state=s.state,
        started_at=s.started_at.isoformat(),
    )


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    body: CreateSessionRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> CreateSessionResponse:
    """Buat session chat baru sekaligus chat pertama dari AI via workflow Langflow."""
    result = await ChatService(session, settings).create_session_with_greeting(
        user_id=current_user.id, agent_code=body.agent_code, category_id=body.category_id
    )
    await session.commit()
    return CreateSessionResponse(**result)


@router.get("/sessions", response_model=list[SessionListItemResponse])
async def list_sessions(
    current_user: CurrentUser,
    session: SessionDep,
    category_id: Annotated[str | None, Query(min_length=1, max_length=26)] = None,
    agent_code: Annotated[Literal["elean", "willy"] | None, Query()] = None,
    state: Annotated[Literal["active", "completed", "abandoned"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[SessionListItemResponse]:
    """Daftar session chat milik user, terfilter kategori/agent/state (owner-scoped)."""
    service = ConversationService(session)
    rows = await service.list_practice_sessions(
        user_id=current_user.id,
        category_id=category_id,
        agent_code=agent_code,
        state=state,
        limit=limit,
    )
    return [
        SessionListItemResponse(
            session_id=conversation.id,
            kind=conversation.kind,
            state=conversation.state,
            category_id=cat_id,
            agent_code=code,
            started_at=conversation.started_at.isoformat(),
            ended_at=conversation.ended_at.isoformat() if conversation.ended_at else None,
        )
        for conversation, cat_id, code in rows
    ]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, current_user: CurrentUser, session: SessionDep
) -> SessionResponse:
    service = ConversationService(session)
    conversation = await service.get_owned_session(
        user_id=current_user.id, session_id=session_id
    )
    response = _session_response(conversation)
    if conversation.kind == "chat":
        practice = await service.get_practice_session(session_id=session_id)
        response.category_id = practice.category_id
        response.agent_code = await service.get_agent_code_for_session(session_id=session_id)
    return response


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str, current_user: CurrentUser, session: SessionDep
) -> None:
    """Hapus session chat beserta seluruh pesan terkait (owner-scoped)."""
    service = ConversationService(session)
    await service.delete_session(user_id=current_user.id, session_id=session_id)
    await session.commit()


@router.post("/sessions/{session_id}/messages", status_code=201)
async def create_message(
    session_id: str,
    body: CreateMessageRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    return await ChatService(session, settings).send(
        user_id=current_user.id,
        session_id=session_id,
        text=body.text,
        client_key=body.client_message_id,
    )


@router.post("/sessions/{session_id}/voice-messages", status_code=201)
async def create_voice_message(
    session_id: str,
    body: CreateVoiceMessageRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    """Kirim voice message; Langflow melakukan STT + generate respons audio."""
    return await ChatService(session, settings).send_voice(
        user_id=current_user.id,
        session_id=session_id,
        media_id=body.media_id,
        text=body.text,
        client_key=body.client_message_id,
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MessageResponse]:
    service = ConversationService(session)
    messages = await service.list_messages(
        user_id=current_user.id, session_id=session_id, limit=limit
    )
    return [
        MessageResponse(
            message_id=m.id,
            session_id=m.session_id,
            role=m.role,
            sequence=m.sequence,
            terminal_state=m.terminal_state,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]


@router.post("/sessions/{session_id}:complete", response_model=SessionResponse)
async def complete_session(
    session_id: str, current_user: CurrentUser, session: SessionDep
) -> SessionResponse:
    service = ConversationService(session)
    conversation = await service.complete_session(
        user_id=current_user.id, session_id=session_id
    )
    await session.commit()
    return _session_response(conversation)


class CategoryResponse(BaseModel):
    category_id: str
    code: str
    title: str
    description: str | None
    image_url: str | None
    sort_order: int


def _category_image_url(settings: Settings, image_key: str | None) -> str | None:
    """Bangun URL publik MinIO (path-style) dari image_key kategori.

    Bucket media development memiliki kebijakan public-read, sehingga klien
    dapat memuat gambar langsung tanpa presigned URL yang kedaluwarsa.
    """
    if not image_key:
        return None
    endpoint = settings.s3_endpoint_url.rstrip("/")
    return f"{endpoint}/{settings.s3_bucket}/{image_key.lstrip('/')}"


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CategoryResponse]:
    """Daftar kategori practice published (public)."""
    from sqlalchemy import select

    rows = (
        (
            await session.execute(
                select(PracticeCategory)
                .where(PracticeCategory.status == "published")
                .order_by(PracticeCategory.sort_order.asc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        CategoryResponse(
            category_id=c.id,
            code=c.code,
            title=c.title,
            description=c.description,
            image_url=_category_image_url(settings, c.image_key),
            sort_order=c.sort_order,
        )
        for c in rows
    ]
