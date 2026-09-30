"""Practice (chat) router (Phase 3): sessions + messages.

Kontrak api-events.md:
- POST /v1/practice/sessions agent_id/category_id → 201
- POST .../{id}/messages text + client_message_id → 201; replay idempoten
- GET .../{id} dan .../{id}/messages owner-scoped (404 cross-owner)
- POST .../{id}:complete → 200

Chat resolves the active PostgreSQL registry entry and calls Langflow.
"""

from __future__ import annotations

from typing import Annotated

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


class CreateMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
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


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    body: CreateSessionRequest, current_user: CurrentUser, session: SessionDep
) -> SessionResponse:
    service = ConversationService(session)
    conversation = await service.start_practice_session(
        user_id=current_user.id, agent_code=body.agent_code, category_id=body.category_id
    )
    await session.commit()
    return _session_response(conversation)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, current_user: CurrentUser, session: SessionDep
) -> SessionResponse:
    service = ConversationService(session)
    conversation = await service.get_owned_session(
        user_id=current_user.id, session_id=session_id
    )
    return _session_response(conversation)


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
