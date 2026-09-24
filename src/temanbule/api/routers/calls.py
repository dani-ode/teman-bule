"""Calls router (Phase 6): create, join-token, end, get.

Join token LiveKit menunggu DEC-14; endpoint mengembalikan state otoritatif
dan gagal eksplisit bila adapter realtime belum terpasang.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.calls.services import CallService
from temanbule.platform.errors import FeatureUnavailableError

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


@router.post("", response_model=CallResponse, status_code=201)
async def create_call(
    body: CreateCallRequest, current_user: CurrentUser, session: SessionDep
) -> CallResponse:
    service = CallService(session)
    call = await service.create_call(
        user_id=current_user.id,
        agent_code=body.agent_code,
        mode=body.mode,
        consent_version=body.consent_version,
    )
    await session.commit()
    return CallResponse(
        session_id=call.session_id,
        mode=call.mode,
        state=call.state,
        room_name=call.room_name,
        end_reason=call.end_reason,
    )


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
