"""Internal calls router (Phase 6): lifecycle admission untuk realtime worker.

Kontrak antara API (pemilik state otoritatif) dan realtime worker (media):
worker meng-admit lease, mengaktifkan session, mencatat turns, dan mengakhiri
session lewat endpoint ini dengan M2M service token (``realtime`` identity).
Fencing token monotonic menolak stale writer setelah restart worker
(realtime-podcast.md: satu lease_owner aktif per session).

Bukan API publik: tidak ada user JWT; caller adalah proses worker tepercaya
di jaringan internal. Response tidak pernah memuat plaintext credential.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.api.deps import SessionDep, SettingsDep, require_service_identity
from temanbule.modules.calls.models import CallTurn
from temanbule.modules.calls.provider_config import load_realtime_provider_config
from temanbule.modules.calls.services import CallService
from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    Plan,
    PlanPolicyVersion,
    RuntimeSnapshot,
)
from temanbule.modules.conversations.models import ConversationSession
from temanbule.platform.errors import ForbiddenError, NotFoundError
from temanbule.platform.settings import ConfigurationError, Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/calls", tags=["internal-calls"])

_REALTIME_SERVICE = "realtime"

ServiceIdentity = Annotated[str, Depends(require_service_identity)]


def _require_realtime(service_identity: str) -> None:
    if service_identity != _REALTIME_SERVICE:
        raise ForbiddenError("Endpoint ini khusus realtime worker.")


async def _conversation(
    session: AsyncSession, session_id: str
) -> ConversationSession:
    conversation = (
        await session.execute(
            select(ConversationSession).where(ConversationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Call tidak ditemukan.")
    return conversation


def _load_persona_instructions(settings: Settings, version: AgentVersion) -> str:
    """Baca persona artifact yang direferensikan agent version.

    ``persona_artifact_ref`` adalah path relatif terhadap
    ``settings.app_persona_base_dir`` dengan hash integritas SHA-256 pada
    ``persona_artifact_hash``. Konten tidak pernah masuk log.
    """
    ref = version.persona_artifact_ref
    if not ref or not ref.strip():
        raise ConfigurationError(["Persona artifact agent version kosong"])
    base = Path(settings.app_persona_base_dir).resolve()
    resolved = (base / ref).resolve()
    if not resolved.is_relative_to(base):
        raise ConfigurationError(["Persona artifact di luar direktori yang diizinkan"])
    if not resolved.is_file():
        raise ConfigurationError(["Persona artifact tidak ditemukan"])
    content = resolved.read_bytes()
    if version.persona_artifact_hash:
        digest = hashlib.sha256(content).hexdigest()
        if digest != version.persona_artifact_hash:
            raise ConfigurationError(["Persona artifact hash tidak cocok"])
    return content.decode("utf-8")


class SessionConfigResponse(BaseModel):
    session_id: str
    room_name: str
    state: str
    agent_code: str
    agent_display_name: str
    persona_instructions: str
    tts_provider: str
    tts_model: str
    tts_voice_id: str
    stt_provider: str
    stt_model: str
    llm_provider: str
    llm_model: str
    plan_code: str
    byok_credential_ref: str | None


class AdmitRequest(BaseModel):
    lease_owner: str = Field(min_length=1, max_length=64)
    lease_seconds: int = Field(gt=0, le=3600)


class LifecycleResponse(BaseModel):
    session_id: str
    state: str
    fencing_token: int
    lease_owner: str | None


class ActivateRequest(BaseModel):
    fencing_token: int = Field(ge=0)
    lease_owner: str = Field(min_length=1, max_length=64)


class TurnRequest(BaseModel):
    fencing_token: int = Field(ge=0)
    lease_owner: str = Field(min_length=1, max_length=64)
    speaker: str = Field(pattern="^(user|agent)$")
    epoch: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=8000)
    interrupted: bool = False


class TurnResponse(BaseModel):
    turn_id: str
    sequence: int
    message_id: str | None


class EndRequest(BaseModel):
    end_reason: str = Field(min_length=1, max_length=40)


@router.get("/{session_id}/config", response_model=SessionConfigResponse)
async def get_session_config(
    session_id: str,
    service_identity: ServiceIdentity,
    session: SessionDep,
    settings: SettingsDep,
) -> SessionConfigResponse:
    """Konfigurasi provider untuk satu call session (tanpa plaintext secret)."""
    _require_realtime(service_identity)
    config = await load_realtime_provider_config(session, settings, session_id=session_id)

    conversation = await _conversation(session, session_id)
    snapshot = (
        await session.execute(
            select(RuntimeSnapshot).where(
                RuntimeSnapshot.id == conversation.runtime_snapshot_id
            )
        )
    ).scalar_one()
    version = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.id == snapshot.agent_version_id)
        )
    ).scalar_one()
    (
        await session.execute(select(Agent).where(Agent.id == version.agent_id))
    ).scalar_one()  # memastikan agent ada; kode/display sudah dari config loader
    policy = (
        await session.execute(
            select(PlanPolicyVersion).where(
                PlanPolicyVersion.id == snapshot.plan_policy_version_id
            )
        )
    ).scalar_one_or_none()
    plan_code = ""
    if policy is not None:
        plan = (
            await session.execute(select(Plan).where(Plan.code == policy.plan_code))
        ).scalar_one_or_none()
        plan_code = plan.code if plan is not None else ""

    call = await CallService(session).get_call(
        user_id=conversation.user_id, session_id=session_id
    )
    return SessionConfigResponse(
        session_id=session_id,
        room_name=config.room_name,
        state=call.state,
        agent_code=config.agent_code,
        agent_display_name=config.agent_display_name,
        persona_instructions=_load_persona_instructions(settings, version),
        tts_provider=config.tts_provider,
        tts_model=config.tts_model,
        tts_voice_id=config.voice_id,
        stt_provider=config.stt_provider,
        stt_model=config.stt_model,
        llm_provider=config.llm_provider,
        llm_model=config.llm_model,
        plan_code=plan_code,
        byok_credential_ref=config.byok_credential_ref,
    )


@router.post("/{session_id}:admit", response_model=LifecycleResponse)
async def admit_call(
    session_id: str,
    body: AdmitRequest,
    service_identity: ServiceIdentity,
    session: SessionDep,
) -> LifecycleResponse:
    _require_realtime(service_identity)
    conversation = await _conversation(session, session_id)
    call = await CallService(session).admit_call(
        user_id=conversation.user_id,
        session_id=session_id,
        lease_owner=body.lease_owner,
        lease_seconds=body.lease_seconds,
    )
    await session.commit()
    return LifecycleResponse(
        session_id=call.session_id,
        state=call.state,
        fencing_token=call.fencing_token,
        lease_owner=call.lease_owner,
    )


@router.post("/{session_id}:activate", response_model=LifecycleResponse)
async def activate_call(
    session_id: str,
    body: ActivateRequest,
    service_identity: ServiceIdentity,
    session: SessionDep,
) -> LifecycleResponse:
    _require_realtime(service_identity)
    conversation = await _conversation(session, session_id)
    call = await CallService(session).activate_call(
        user_id=conversation.user_id,
        session_id=session_id,
        fencing_token=body.fencing_token,
        lease_owner=body.lease_owner,
    )
    await session.commit()
    return LifecycleResponse(
        session_id=call.session_id,
        state=call.state,
        fencing_token=call.fencing_token,
        lease_owner=call.lease_owner,
    )


@router.post("/{session_id}/turns", response_model=TurnResponse, status_code=201)
async def record_turn(
    session_id: str,
    body: TurnRequest,
    service_identity: ServiceIdentity,
    session: SessionDep,
) -> TurnResponse:
    _require_realtime(service_identity)
    conversation = await _conversation(session, session_id)
    service = CallService(session)
    turn = await service.record_turn(
        user_id=conversation.user_id,
        session_id=session_id,
        fencing_token=body.fencing_token,
        lease_owner=body.lease_owner,
        speaker=body.speaker,
        epoch=body.epoch,
    )
    message_id: str | None = None
    if body.text:
        from temanbule.modules.calls.transcripts import append_turn_message

        message = await append_turn_message(
            session,
            session_id=session_id,
            owner_user_id=conversation.user_id,
            speaker=body.speaker,
            text=body.text,
            interrupted=body.interrupted,
        )
        turn.message_id = message.id
        message_id = message.id
    if body.interrupted:
        turn = await service.interrupt_turn(
            user_id=conversation.user_id,
            session_id=session_id,
            turn_id=turn.id,
            fencing_token=body.fencing_token,
            lease_owner=body.lease_owner,
        )
    await session.commit()
    return TurnResponse(turn_id=turn.id, sequence=turn.sequence, message_id=message_id)


@router.get("/{session_id}/turns", response_model=list[TurnResponse])
async def list_turns(
    session_id: str,
    service_identity: ServiceIdentity,
    session: SessionDep,
) -> list[TurnResponse]:
    """Checkpoint recovery: turns yang sudah tercatat untuk session ini."""
    _require_realtime(service_identity)
    await _conversation(session, session_id)
    turns = (
        await session.execute(
            select(CallTurn)
            .where(CallTurn.session_id == session_id)
            .order_by(CallTurn.sequence)
        )
    ).scalars()
    return [
        TurnResponse(turn_id=turn.id, sequence=turn.sequence, message_id=turn.message_id)
        for turn in turns
    ]


@router.post("/{session_id}:end", response_model=LifecycleResponse)
async def end_call_internal(
    session_id: str,
    body: EndRequest,
    service_identity: ServiceIdentity,
    session: SessionDep,
) -> LifecycleResponse:
    _require_realtime(service_identity)
    conversation = await _conversation(session, session_id)
    call = await CallService(session).end_call(
        user_id=conversation.user_id, session_id=session_id, end_reason=body.end_reason
    )
    await session.commit()
    return LifecycleResponse(
        session_id=call.session_id,
        state=call.state,
        fencing_token=call.fencing_token,
        lease_owner=call.lease_owner,
    )
