"""Calls router (Phase 6): create, join-token, end, get, list.

Join token LiveKit menunggu DEC-14; endpoint mengembalikan state otoritatif
dan gagal eksplisit bila adapter realtime belum terpasang.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.api.routers.plans import _agent_image_url
from temanbule.modules.calls.models import CallSession
from temanbule.modules.calls.services import CallService
from temanbule.modules.catalog.models import Agent, AgentVersion, RuntimeSnapshot
from temanbule.modules.conversations.models import ConversationSession
from temanbule.platform.errors import DependencyUnavailableError, FeatureUnavailableError

router = APIRouter(prefix="/v1/calls", tags=["calls"])


class CreateCallRequest(BaseModel):
    mode: str = Field(pattern="^(voice|video)$")
    agent_code: str = Field(pattern="^(elean|willy)$")
    consent_version: str = Field(min_length=1, max_length=40)


class CallResponse(BaseModel):
    session_id: str
    mode: str
    state: str
    room_name: str
    end_reason: str | None


class CallListItemResponse(BaseModel):
    session_id: str
    mode: str
    state: str
    agent_code: str
    agent_display_name: str
    agent_profile_image_url: str | None
    started_at: str
    ended_at: str | None
    duration_seconds: int | None


@router.post("", response_model=CallResponse, status_code=201)
async def create_call(
    body: CreateCallRequest, current_user: CurrentUser, session: SessionDep, request: Request
) -> CallResponse:
    service = CallService(session)
    call = await service.create_call(
        user_id=current_user.id,
        agent_code=body.agent_code,
        mode=body.mode,
        consent_version=body.consent_version,
    )
    await session.commit()
    # Explicit agent dispatch: room baru dibuat saat peserta pertama join;
    # tanpa dispatch, LiveKit tidak mengirim agent dan peserta menunggu.
    # Dispatch gagal → call dibatalkan dengan reason eksplisit agar tidak ada
    # session yatim yang menunggu agent tanpa batas (fail-fast, DEC-14).
    dispatch = getattr(request.app.state, "livekit_agent_dispatch", None)
    if not callable(dispatch):
        raise FeatureUnavailableError(
            "Realtime agent dispatch belum dikonfigurasi (menunggu DEC-14).",
        )
    try:
        await dispatch(room_name=call.room_name, session_id=call.session_id)
    except DependencyUnavailableError:
        await service.end_call(
            user_id=current_user.id, session_id=call.session_id, end_reason="error"
        )
        await session.commit()
        raise
    return CallResponse(
        session_id=call.session_id,
        mode=call.mode,
        state=call.state,
        room_name=call.room_name,
        end_reason=call.end_reason,
    )


@router.get("", response_model=list[CallListItemResponse])
async def list_calls(
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    state: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CallListItemResponse]:
    """List call history for the current user, newest first."""
    stmt = (
        select(CallSession, ConversationSession, Agent)
        .join(ConversationSession, CallSession.session_id == ConversationSession.id)
        .join(RuntimeSnapshot, ConversationSession.runtime_snapshot_id == RuntimeSnapshot.id)
        .join(AgentVersion, RuntimeSnapshot.agent_version_id == AgentVersion.id)
        .join(Agent, AgentVersion.agent_id == Agent.id)
        .where(
            ConversationSession.user_id == current_user.id,
            ConversationSession.kind == "call",
        )
        .order_by(ConversationSession.started_at.desc())
        .limit(min(limit, 100))
    )
    if state:
        stmt = stmt.where(ConversationSession.state == state)

    rows = (await session.execute(stmt)).all()

    items: list[CallListItemResponse] = []
    for call, conv, agent in rows:
        duration: int | None = None
        if conv.started_at and conv.ended_at:
            duration = int((conv.ended_at - conv.started_at).total_seconds())

        items.append(
            CallListItemResponse(
                session_id=call.session_id,
                mode=call.mode,
                state=call.state,
                agent_code=agent.code,
                agent_display_name=agent.display_name,
                agent_profile_image_url=_agent_image_url(settings, agent.profile_image_key),
                started_at=conv.started_at.isoformat(),
                ended_at=conv.ended_at.isoformat() if conv.ended_at else None,
                duration_seconds=duration,
            )
        )
    return items


@router.get("/{session_id}", response_model=CallResponse)
async def get_call(
    session_id: str, current_user: CurrentUser, session: SessionDep
) -> CallResponse:
    service = CallService(session)
    call = await service.get_call(user_id=current_user.id, session_id=session_id)
    return CallResponse(
        session_id=call.session_id,
        mode=call.mode,
        state=call.state,
        room_name=call.room_name,
        end_reason=call.end_reason,
    )


@router.post("/{session_id}:join-token")
async def join_token(
    session_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, str]:
    service = CallService(session)
    await service.get_call(user_id=current_user.id, session_id=session_id)
    factory = getattr(request.app.state, "livekit_token_factory", None)
    if not callable(factory):
        raise FeatureUnavailableError(
            "Realtime admission belum dikonfigurasi (menunggu DEC-14).",
        )
    return {"join_token": factory(session_id, current_user.id)}


class EndCallRequest(BaseModel):
    end_reason: str = Field(min_length=1, max_length=40)


@router.post("/{session_id}:end", response_model=CallResponse)
async def end_call(
    session_id: str,
    body: EndCallRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> CallResponse:
    service = CallService(session)
    call = await service.end_call(
        user_id=current_user.id, session_id=session_id, end_reason=body.end_reason
    )
    await session.commit()
    return CallResponse(
        session_id=call.session_id,
        mode=call.mode,
        state=call.state,
        room_name=call.room_name,
        end_reason=call.end_reason,
    )
