"""Call session lifecycle untuk realtime worker (Phase 6 / DEC-14).

Satu ``CallSessionRunner`` menangani satu job LiveKit (satu room call):

1. ``admit`` lease + fencing token dari internal API (API otoritatif).
2. Bangun pipeline VAD (silero lokal) → STT → LLM → TTS ElevenLabs sesuai
   konfigurasi session (VIP platform key; BYOK gagal eksplisit sampai
   credential broker one-use tersedia untuk worker).
3. ``activate`` setelah pipeline siap, catat turns final (user/agent) lewat
   internal API dengan fencing, dan akhiri session dengan end_reason
   eksplisit saat room tutup / error / max duration.

Barge-in/interupsi ditangani AgentSession (allow_interruptions=True): speech
lama dibatalkan framework; turn yang diinterupsi dicatat ``interrupted``
tanpa menghapus turn (realtime-podcast.md).

Worker TIDAK menulis DB secara langsung dan tidak pernah melog transcript,
api key, maupun metadata pengguna.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from livekit.agents import inference
from livekit.agents.job import JobContext
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)
from livekit.agents.voice.room_io import RoomOptions

from temanbule.platform.settings import Settings, load_settings
from temanbule.realtime.api_client import RealtimeApiClient
from temanbule.realtime.llm_adapter import OpenAICompatibleLLM

logger = logging.getLogger(__name__)

# Provider LLM LiveKit Cloud Inference (STT/TTS); diautentikasi LIVEKIT_API_KEY/SECRET
# server yang sama — bukan credential pengguna.
_LIVEKIT_STT_MODELS = {"gemini": "google/gemini-3.5-transcribe-live", "openai": "openai/whisper-1"}
_LIVEKIT_TTS_MODELS = {"elevenlabs": "elevenlabs"}

_MAX_TURNS_KEPT = 40


class CallSessionRunner:
    """Menjalankan satu call session dari admission sampai end."""

    def __init__(self, settings: Settings, ctx: JobContext) -> None:
        self._settings = settings
        self._ctx = ctx
        self._api = RealtimeApiClient(settings)
        self._lease_owner = f"rtw-{uuid.uuid4().hex[:24]}"
        self._fencing_token = 0
        self._epoch = 0
        self._session: AgentSession[None] | None = None
        self._ended = False
        self._user_transcript_buffer = ""
        self._turns_recorded = 0

    async def run(self) -> None:
        metadata = (self._ctx.job.metadata or "").strip()
        if not metadata:
            logger.error("job_metadata_missing")
            await self._ctx.room.disconnect()
            return
        session_id = metadata
        try:
            config = await self._api.get_session_config(session_id)
            await self._run_lifecycle(session_id, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("call_session_failed")
            await self._safe_end(session_id, end_reason="error")
            raise
        finally:
            await self._api.aclose()

    async def _run_lifecycle(self, session_id: str, config: dict[str, Any]) -> None:
        if config["state"] == "ended":
            logger.info("call_already_ended")
            await self._ctx.room.disconnect()
            return
        if config.get("byok_credential_ref"):
            # Credential BYOK untuk media call memerlukan broker one-use yang
            # belum tersedia untuk worker; gagal eksplisit tanpa fallback.
            logger.error("byok_call_unsupported")
            await self._safe_end(session_id, end_reason="error")
            await self._ctx.room.disconnect()
            return

        admitted = await self._api.admit(
            session_id,
            lease_owner=self._lease_owner,
            lease_seconds=self._settings.realtime_lease_ttl_seconds,
        )
        self._fencing_token = int(admitted["fencing_token"])

        await self._ctx.connect()
        self._wire_room_handlers(session_id)

        session = self._build_agent_session(config)
        self._session = session
        self._wire_session_events(session, session_id)

        await self._api.activate(
            session_id,
            fencing_token=self._fencing_token,
            lease_owner=self._lease_owner,
        )
        logger.info("call_activated", extra={"agent": config["agent_code"]})

        agent = Agent(instructions=config["persona_instructions"])
        max_duration = self._settings.call_max_duration_seconds
        duration_task = (
            asyncio.create_task(self._enforce_max_duration(session_id, max_duration))
            if max_duration > 0
            else None
        )
        try:
            await session.start(
                agent,
                room=self._ctx.room,
                room_options=RoomOptions(delete_room_on_close=False),
            )
        finally:
            if duration_task is not None:
                duration_task.cancel()

    def _build_agent_session(self, config: dict[str, Any]) -> AgentSession[None]:
        settings = self._settings
        vad = inference.VAD(
            min_silence_duration=max(settings.call_vad_silence_ms, 100) / 1000.0,
        )
        stt_provider = config["stt_provider"]
        stt_model = _LIVEKIT_STT_MODELS.get(stt_provider)
        if stt_model is None:
            raise ValueError(f"Provider STT tidak didukung: {stt_provider}")
        stt = inference.STT(
            model=stt_model,
            base_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        if config["tts_provider"] != "elevenlabs":
            raise ValueError(f"Provider TTS tidak didukung: {config['tts_provider']}")
        tts = inference.TTS(
            model=_LIVEKIT_TTS_MODELS["elevenlabs"],
            voice=config["tts_voice_id"],
            base_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        llm = OpenAICompatibleLLM(
            provider=config["llm_provider"],
            model=config["llm_model"],
            base_url=settings.provider_base_url(config["llm_provider"]),
            api_key=(
                settings.gemini_api_key
                if config["llm_provider"] == "gemini"
                else settings.openai_api_key
            ),
            timeout_seconds=settings.langflow_timeout_seconds,
        )
        return AgentSession[None](
            stt=stt,
            llm=llm,
            tts=tts,
            vad=vad,
            allow_interruptions=True,
        )

    def _wire_session_events(self, session: AgentSession[None], session_id: str) -> None:
        @session.on("user_input_transcribed")
        def _on_user_transcript(event: UserInputTranscribedEvent) -> None:
            if event.is_final:
                self._epoch += 1
                self._schedule_turn(
                    session_id,
                    speaker="user",
                    text=event.transcript,
                    interrupted=False,
                    epoch=self._epoch,
                )

        @session.on("conversation_item_added")
        def _on_conversation_item(event: ConversationItemAddedEvent) -> None:
            item = event.item
            if getattr(item, "role", None) != "assistant":
                return
            text = getattr(item, "text_content", None) or ""
            if text.strip():
                self._schedule_turn(
                    session_id,
                    speaker="agent",
                    text=text,
                    interrupted=bool(getattr(item, "interrupted", False)),
                    epoch=self._epoch,
                )

        @session.on("agent_state_changed")
        def _on_agent_state(event: AgentStateChangedEvent) -> None:
            logger.info("agent_state", extra={"state": str(event.new_state)})

    def _wire_room_handlers(self, session_id: str) -> None:
        room = self._ctx.room

        @room.on("disconnected")
        def _on_disconnected(reason: Any) -> None:
            logger.info("room_disconnected", extra={"reason_code": str(reason)})
            if not self._ended:
                self._ended = True
                asyncio.create_task(self._safe_end(session_id, end_reason="user_hangup"))

    def _schedule_turn(
        self,
        session_id: str,
        *,
        speaker: str,
        text: str,
        interrupted: bool,
        epoch: int,
    ) -> None:
        if self._ended:
            return
        self._turns_recorded += 1
        if self._turns_recorded > _MAX_TURNS_KEPT:
            # Guard pathological loop; API tetap otoritas penuh.
            logger.warning("turn_budget_exceeded")

        async def _record() -> None:
            try:
                await self._api.record_turn(
                    session_id,
                    fencing_token=self._fencing_token,
                    lease_owner=self._lease_owner,
                    speaker=speaker,
                    epoch=epoch,
                    text=text,
                    interrupted=interrupted,
                )
            except Exception:
                logger.exception("turn_record_failed")

        asyncio.create_task(_record())

    async def _enforce_max_duration(self, session_id: str, max_seconds: int) -> None:
        await asyncio.sleep(max_seconds)
        logger.info("call_max_duration_reached")
        await self._shutdown_session(session_id, end_reason="timeout")

    async def _safe_end(self, session_id: str, *, end_reason: str) -> None:
        if self._ended:
            return
        self._ended = True
        try:
            await self._api.end(session_id, end_reason=end_reason)
        except Exception:
            logger.exception("call_end_failed")

    async def _shutdown_session(self, session_id: str, *, end_reason: str) -> None:
        await self._safe_end(session_id, end_reason=end_reason)
        if self._session is not None:
            try:
                await self._session.aclose()
            except Exception:
                logger.exception("session_close_failed")
        try:
            await self._ctx.room.disconnect()
        except Exception:
            logger.exception("room_disconnect_failed")


def build_entrypoint(settings: Settings) -> Callable[[JobContext], Awaitable[None]]:
    """Factory entrypoint terikat settings — HANYA untuk in-process usage (tests).

    Proses job LiveKit memakai multiprocessing (forkserver/spawn) sehingga
    callable closure tidak dapat di-pickle. Worker produksi memakai
    ``agent_entrypoint`` module-level dengan settings lazy per-proses.
    """

    async def entrypoint(ctx: JobContext) -> None:
        runner = CallSessionRunner(settings, ctx)
        await runner.run()

    return entrypoint


async def agent_entrypoint(ctx: JobContext) -> None:
    """Entrypoint produksi (pickle-able, module-level).

    Settings dimuat sekali per proses job (forkserver/spawn) dan di-cache
    pada module global proses tersebut — konfigurasi tetap satu sumber
    kebenaran dari environment.
    """
    global _process_settings
    settings = _process_settings
    if settings is None:
        settings = load_settings(validate=False)
        settings.validate_for_realtime_worker()
        _process_settings = settings
    runner = CallSessionRunner(settings, ctx)
    await runner.run()


_process_settings: Settings | None = None
