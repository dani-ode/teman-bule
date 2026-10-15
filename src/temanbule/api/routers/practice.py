"""Practice (chat) router (Phase 3): sessions + messages.

Kontrak api-events.md:
- POST /v1/practice/sessions agent_id/category_id → 201
- POST .../{id}/messages text + client_message_id → 201; replay idempoten
- GET .../{id} dan .../{id}/messages owner-scoped (404 cross-owner)
- POST .../{id}:complete → 200

Chat resolves the active PostgreSQL registry entry and calls Langflow.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.modules.conversations.chat import ChatService
from temanbule.modules.conversations.langflow import ChatStreamEvent
from temanbule.modules.conversations.models import ConversationSession, PracticeCategory
from temanbule.modules.conversations.services import ConversationService
from temanbule.platform.errors import AppError
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)

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
    # Teks transkrip hasil POST /v1/media/{media_id}:transcribe (alur 3-hit);
    # wajib agar pesan user tersimpan dengan teks final sejak INSERT.
    text: str = Field(min_length=1, max_length=8000)
    # Durasi audio dari client (recorder) — disimpan saat INSERT, append-only.
    audio_duration_ms: int | None = Field(default=None, ge=0)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    sequence: int
    terminal_state: str
    created_at: str
    # Konten pesan untuk render di klien (chat history).
    text: str | None
    modality: str | None
    # Link ke media_objects untuk audio yang terdaftar (voice message user);
    # klien dapat me-refresh URL lewat GET /v1/media/{media_id}/download-url.
    media_id: str | None
    # URL audio siap putar: presigned GET (media terdaftar) atau URL publik
    # bucket chat (audio agent hasil TTS, bila bucket public-read).
    audio_url: str | None
    audio_duration_ms: int | None


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


@router.post("/sessions:stream")
async def create_session_stream(
    body: CreateSessionRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Buat session + greeting via SSE (kontrak api-events.md Streaming).

    Event: ``started`` → ``text_delta`` (teks greeting, tersedia sebelum
    TTS) → ``usage_updated`` → terminal ``completed`` (berisi session_id,
    first_message, dst — payload identik respons sync) atau ``failed``.
    Session tetap ter-commit apa pun hasil greeting-nya (best-effort).
    """

    async def chunks() -> AsyncIterator[str]:
        service = ChatService(session, settings)
        event_id = 0
        try:
            async for event in service.create_session_with_greeting_stream(
                user_id=current_user.id,
                agent_code=body.agent_code,
                category_id=body.category_id,
            ):
                event_id += 1
                yield _format_sse(event_id, event.kind, _event_payload(event))
                if event.kind in ("completed", "failed"):
                    await _safe_commit(session)
                    return
        except AppError as exc:
            event_id += 1
            yield _sse_error_event(event_id, exc)
            await _safe_commit(session)

    return _sse_response(chunks())


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
    result = await ChatService(session, settings).send(
        user_id=current_user.id,
        session_id=session_id,
        text=body.text,
        client_key=body.client_message_id,
    )
    await session.commit()
    return result


@router.post("/sessions/{session_id}/voice-messages", status_code=201)
async def create_voice_message(
    session_id: str,
    body: CreateVoiceMessageRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, object]:
    """Kirim voice message; teks transkrip wajib (hasil transcribe), respons audio via TTS."""
    result = await ChatService(session, settings).send_voice(
        user_id=current_user.id,
        session_id=session_id,
        media_id=body.media_id,
        text=body.text,
        client_key=body.client_message_id,
    )
    await session.commit()
    return result


# --- Chat SSE (Workflow API mode="stream") ---------------------------------
#
# Kontrak api-events.md: started → nol/lebih transcript/text_delta/
# usage_updated → tepat satu terminal (completed/failed). Teks jawaban dan
# transcript STT tersedia jauh sebelum TTS+upload selesai, sehingga klien
# tidak lagi menunggu respons penuh seperti mode sync.
#
# Event id berupa sequence per koneksi (1, 2, ...); replay Last-Event-ID
# belum didukung — reconnect setelah terminal cukup mengambil history via
# GET .../messages (kontrak api-events.md: reconnect tidak pernah
# menjalankan ulang provider invocation).

def _format_sse(event_id: int, event: str, data: dict[str, Any]) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_error_event(event_id: int, exc: AppError) -> str:
    """Kegagalan sebelum stream terbuka tetap disampaikan sebagai event SSE."""
    return _format_sse(event_id, "failed", {
        "code": exc.code,
        "message": str(exc),
        "status_code": exc.status_code,
    })


async def _safe_commit(session: Any) -> None:
    """Commit dengan proteksi: kegagalan DB tidak boleh memutus SSE secara kasar.

    Kegagalan commit (mis. DBAPIError) dicatat dan session di-rollback agar
    koneksi bersih; stream sudah selesai mengirim event terminal sehingga
    client tidak terganggu oleh exception yang bocor ke ASGI.
    """
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 — jangan biarkan exception DB memutus stream
        logger.exception("gagal commit setelah terminal SSE event")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("gagal rollback setelah commit SSE gagal")


def _sse_response(chunks: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        chunks,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nonaktifkan buffering proxy (nginx/Apache) agar event tiba bertahap.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/messages:stream")
async def create_message_stream(
    session_id: str,
    body: CreateMessageRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Chat turn teks via SSE (kontrak api-events.md Streaming).

    Event: ``started`` → ``text_delta`` (respons teks LLM, tersedia sebelum
    TTS) → ``usage_updated`` → terminal ``completed`` (berisi message_id,
    audio_url, dst — payload identik respons sync) atau ``failed``.
    """

    async def chunks() -> AsyncIterator[str]:
        service = ChatService(session, settings)
        event_id = 0
        try:
            async for event in service.send_stream(
                user_id=current_user.id,
                session_id=session_id,
                text=body.text,
                client_key=body.client_message_id,
            ):
                event_id += 1
                yield _format_sse(event_id, event.kind, _event_payload(event))
                if event.kind in ("completed", "failed"):
                    await _safe_commit(session)
                    return
        except AppError as exc:
            event_id += 1
            yield _sse_error_event(event_id, exc)
            await _safe_commit(session)

    return _sse_response(chunks())


@router.post("/sessions/{session_id}/voice-messages:stream")
async def create_voice_message_stream(
    session_id: str,
    body: CreateVoiceMessageRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Voice message via SSE: teks/audio respons agent tampil bertahap.

    Teks transkrip user sudah final di body request (alur 3-hit), sehingga
    tidak ada lagi event ``transcript`` dari STT di dalam flow chat_turn.
    """

    async def chunks() -> AsyncIterator[str]:
        service = ChatService(session, settings)
        event_id = 0
        try:
            async for event in service.send_voice_stream(
                user_id=current_user.id,
                session_id=session_id,
                media_id=body.media_id,
                text=body.text,
                audio_duration_ms=body.audio_duration_ms,
                client_key=body.client_message_id,
            ):
                event_id += 1
                yield _format_sse(event_id, event.kind, _event_payload(event))
                if event.kind in ("completed", "failed"):
                    await _safe_commit(session)
                    return
        except AppError as exc:
            event_id += 1
            yield _sse_error_event(event_id, exc)
            await _safe_commit(session)

    return _sse_response(chunks())


def _event_payload(event: ChatStreamEvent) -> dict[str, Any]:
    payload: dict[str, Any] = dict(event.data)
    if event.text is not None:
        payload["text"] = event.text
    if event.usage is not None:
        payload["usage"] = event.usage
    if event.error is not None:
        payload.setdefault("message", event.error)
    return payload


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MessageResponse]:
    """History room chat (owner-scoped).

    Pesan audio disertai ``audio_url`` siap putar: voice message user
    (media_id terdaftar) di-presign per pesan; audio agent hasil TTS
    di-resolve dari storage key stabil bila bucket chat public-read.
    """
    service = ConversationService(session, settings)
    messages = await service.list_messages(
        user_id=current_user.id, session_id=session_id, limit=limit
    )
    download_url_factory = getattr(request.app.state, "media_download_url_factory", None)
    audio_urls = await service.resolve_message_audio_urls(
        messages,
        download_url_factory if callable(download_url_factory) else None,
    )
    return [
        MessageResponse(
            message_id=m.id,
            session_id=m.session_id,
            role=m.role,
            sequence=m.sequence,
            terminal_state=m.terminal_state,
            created_at=m.created_at.isoformat(),
            text=m.text,
            modality=m.modality,
            media_id=m.media_id,
            audio_url=audio_urls.get(m.id),
            audio_duration_ms=m.audio_duration_ms,
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
