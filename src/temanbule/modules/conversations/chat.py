"""Connect the authenticated chat route to its PostgreSQL-registered workflow."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.chat_context import chat_context
from temanbule.modules.catalog.flows import FlowBinding, resolve_flow
from temanbule.modules.conversations.langflow import (
    ChatResult,
    ChatStreamEvent,
    LangflowChatAdapter,
)
from temanbule.modules.conversations.models import PracticeSession
from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.conversations.session_create import (
    LangflowSessionCreateAdapter,
    SessionCreateResult,
)
from temanbule.modules.conversations.suggestion import (
    LangflowSuggestionAdapter,
    SuggestionResult,
)
from temanbule.modules.media.models import MediaObject
from temanbule.platform.errors import (
    AppError,
    ConflictError,
    FeatureUnavailableError,
    NotFoundError,
    ValidationError,
)
from temanbule.platform.security import new_ulid
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PreparedTurn:
    """Konteks chat turn yang sudah tervalidasi, siap dieksekusi adapter."""

    user_id: str
    session_id: str
    binding: FlowBinding
    envelope: dict[str, Any]
    practice: PracticeSession
    turn_input: dict[str, Any]
    client_key: str | None


class ChatService:
    def __init__(self, session: AsyncSession, settings: Settings,
                 adapter: LangflowChatAdapter | None = None,
                 session_create_adapter: LangflowSessionCreateAdapter | None = None,
                 suggestion_adapter: LangflowSuggestionAdapter | None = None):
        self.session = session
        self.settings = settings
        self.adapter = adapter or LangflowChatAdapter(settings)
        self.session_create_adapter = session_create_adapter or LangflowSessionCreateAdapter(
            settings
        )
        self.suggestion_adapter = suggestion_adapter or LangflowSuggestionAdapter(settings)
        # Storage key audio TTS yang sudah direlokasi per session (session chat
        # single-user sehingga turn diproses sekuensial). Hanya bookkeeping
        # internal; resolusi history memakai key stabil turunan message_id.
        self._last_object_key_by_session: dict[str, tuple[str, str]] = {}

    async def create_session_with_greeting(
        self, *, user_id: str, agent_code: str, category_id: str
    ) -> dict[str, Any]:
        """Buat session chat baru sekaligus chat pertama dari AI via Langflow.

        Flow: buat session (snapshot terverifikasi) → resolve flow registry
        purpose 'chat_session_create' → kirim payload JSON minimal ke Langflow
        → simpan response_text sebagai message pertama role='agent'.

        Greeting best-effort: jika flow belum terdaftar / gagal, session tetap
        terbuat dan first_message=None agar klien tetap bisa masuk room.
        """
        conversations = ConversationService(self.session, self.settings)
        conversation = await conversations.start_practice_session(
            user_id=user_id, agent_code=agent_code, category_id=category_id,
        )
        practice = await conversations.get_practice_session(session_id=conversation.id)

        first_message: dict[str, Any] | None = None
        try:
            # begin_nested() = SAVEPOINT: bila greeting gagal, hanya perubahan
            # di dalam blok ini yang di-rollback; session + practice (di-flush
            # sebelumnya) TETAP ada, sehingga router.commit() menyimpan session
            # dan room chat tidak 404.
            async with self.session.begin_nested():
                if not self.settings.feature_ai_enabled:
                    raise FeatureUnavailableError("AI sedang dinonaktifkan.")
                binding = await resolve_flow(
                    self.session,
                    environment=self.settings.app_env,
                    purpose="chat_session_create",
                )
                context = await chat_context(
                    self.session, conversation.runtime_snapshot_id, user_id
                )
                envelope = {
                    "schema_version": binding.input_schema_version,
                    "request_id": new_ulid(),
                    "purpose": "chat_session_create",
                    "user": {"user_id": user_id},
                    "session": {
                        "session_id": conversation.id,
                        "runtime_snapshot_id": conversation.runtime_snapshot_id,
                    },
                    "persona": context["persona"],
                    "ai_configuration": context["ai_configuration"],
                    "input": {
                        "agent_code": context["agent_code"],
                        "category_id": category_id,
                    },
                }
                result = await self.session_create_adapter.run(binding, envelope)
                # Simpan media_id bila workflow mengembalikan object_key audio
                greeting_media_id = await self._resolve_media_id_from_result(result)
                message = await conversations.append_agent_message(
                    session_id=conversation.id,
                    owner_user_id=user_id,
                    agent_version_id=practice.agent_version_id,
                    text=result.response_text,
                    # Chat mostly audio: greeting disimpan sebagai modality 'audio'
                    # bila flow mengembalikan URL audio S3/MinIO hasil TTS.
                    modality="audio" if result.audio_url else "text",
                    media_id=greeting_media_id,
                    audio_duration_ms=result.audio_duration_ms,
                )
                # Pindahkan object TTS ke key stabil turunan message_id agar
                # history bisa me-resolve URL tanpa live response (best-effort).
                if greeting_media_id is None:
                    await self._relocate_agent_audio(
                        conversation.id, message.id, result
                    )
                first_message = {
                    "message_id": message.id,
                    "session_id": message.session_id,
                    "role": message.role,
                    "text": message.text,
                    "modality": message.modality,
                    "media_id": message.media_id,
                    "audio_url": result.audio_url,
                    "audio_duration_ms": result.audio_duration_ms,
                    "sequence": message.sequence,
                    "terminal_state": message.terminal_state,
                    "created_at": message.created_at.isoformat(),
                }
        except AppError as exc:
            logger.warning(
                "Session-create greeting skipped (session kept): %s", exc.code
            )

        return {
            "session_id": conversation.id,
            "kind": conversation.kind,
            "state": conversation.state,
            "started_at": conversation.started_at.isoformat(),
            "agent_code": agent_code,
            "category_id": category_id,
            "first_message": first_message,
        }

    async def create_session_with_greeting_stream(
        self, *, user_id: str, agent_code: str, category_id: str
    ) -> AsyncIterator[ChatStreamEvent]:
        """Buat session + greeting via SSE: teks sapaan tampil sebelum TTS selesai.

        Session dibuat dan di-flush SEBELUM stream dibuka, sehingga room chat
        tetap ada (dan dapat di-commit router) apa pun hasil greeting-nya.
        Greeting sendiri best-effort: bila flow belum terdaftar/gagal,
        terminal ``failed`` dikirim dengan first_message=None — client cukup
        masuk room tanpa pesan pembuka.

        Tidak ada retry/replay: greeting one-shot per session; client yang
        terputus cukup mengambil history via GET .../messages.
        """
        conversations = ConversationService(self.session, self.settings)
        conversation = await conversations.start_practice_session(
            user_id=user_id, agent_code=agent_code, category_id=category_id,
        )
        practice = await conversations.get_practice_session(session_id=conversation.id)

        first_message: dict[str, Any] | None = None
        try:
            # begin_nested() = SAVEPOINT: bila greeting gagal, hanya perubahan
            # di dalam blok ini yang di-rollback; session + practice (di-flush
            # sebelumnya) TETAP ada — identik jalur sync.
            async with self.session.begin_nested():
                if not self.settings.feature_ai_enabled:
                    raise FeatureUnavailableError("AI sedang dinonaktifkan.")
                binding = await resolve_flow(
                    self.session,
                    environment=self.settings.app_env,
                    purpose="chat_session_create",
                )
                context = await chat_context(
                    self.session, conversation.runtime_snapshot_id, user_id
                )
                envelope = {
                    "schema_version": binding.input_schema_version,
                    "request_id": new_ulid(),
                    "purpose": "chat_session_create",
                    "user": {"user_id": user_id},
                    "session": {
                        "session_id": conversation.id,
                        "runtime_snapshot_id": conversation.runtime_snapshot_id,
                    },
                    "persona": context["persona"],
                    "ai_configuration": context["ai_configuration"],
                    "input": {
                        "agent_code": context["agent_code"],
                        "category_id": category_id,
                    },
                }
                upstream = self.session_create_adapter.run_stream(binding, envelope)
                async for event in upstream:
                    if event.kind == "completed" and event.result is not None:
                        result = event.result
                        greeting_media_id = await self._resolve_media_id_from_result(
                            result
                        )
                        message = await conversations.append_agent_message(
                            session_id=conversation.id,
                            owner_user_id=user_id,
                            agent_version_id=practice.agent_version_id,
                            text=result.response_text,
                            # Chat mostly audio: greeting disimpan sebagai
                            # modality 'audio' bila flow mengembalikan TTS.
                            modality="audio" if result.audio_url else "text",
                            media_id=greeting_media_id,
                            audio_duration_ms=result.audio_duration_ms,
                        )
                        # Key stabil turunan message_id agar history bisa
                        # me-resolve URL tanpa live response (best-effort).
                        if greeting_media_id is None:
                            await self._relocate_agent_audio(
                                conversation.id, message.id, result
                            )
                        first_message = {
                            "message_id": message.id,
                            "session_id": message.session_id,
                            "role": message.role,
                            "text": message.text,
                            "modality": message.modality,
                            "media_id": message.media_id,
                            "audio_url": result.audio_url,
                            "audio_duration_ms": result.audio_duration_ms,
                            "sequence": message.sequence,
                            "terminal_state": message.terminal_state,
                            "created_at": message.created_at.isoformat(),
                        }
                        event.data.update({
                            "session_id": conversation.id,
                            "kind": conversation.kind,
                            "state": conversation.state,
                            "started_at": conversation.started_at.isoformat(),
                            "agent_code": agent_code,
                            "category_id": category_id,
                            "first_message": first_message,
                        })
                    elif event.kind == "failed":
                        event.data.update({
                            "session_id": conversation.id,
                            "kind": conversation.kind,
                            "state": conversation.state,
                            "started_at": conversation.started_at.isoformat(),
                            "agent_code": agent_code,
                            "category_id": category_id,
                            "first_message": None,
                        })
                    yield event
        except AppError as exc:
            logger.warning(
                "Session-create greeting skipped (session kept): %s", exc.code
            )
            yield ChatStreamEvent(
                kind="failed",
                error=str(exc),
                data={
                    "code": exc.code,
                    "session_id": conversation.id,
                    "kind": conversation.kind,
                    "state": conversation.state,
                    "started_at": conversation.started_at.isoformat(),
                    "agent_code": agent_code,
                    "category_id": category_id,
                    "first_message": None,
                },
            )

    async def get_suggestions(
        self, *, user_id: str, session_id: str
    ) -> SuggestionResult:
        """Ambil 3 suggestion pesan untuk user via flow Langflow suggestion_message.

        Flow menerima konteks session (session_id + category_id + history
        ringkas) dan mengembalikan 3 teks suggestion. Tidak ada persistensi
        — suggestion bersifat ephemeral dan bukan bagian dari history chat.
        """
        if not self.settings.feature_ai_enabled:
            raise FeatureUnavailableError("AI sedang dinonaktifkan.")
        conversations = ConversationService(self.session, self.settings)
        conversation = await conversations.get_owned_session(
            user_id=user_id, session_id=session_id,
        )
        if conversation.state != "active":
            raise ConflictError("Session sudah tidak aktif.", code="SESSION_CLOSED")
        binding = await resolve_flow(
            self.session,
            environment=self.settings.app_env,
            purpose="chat_suggestion",
        )
        context = await chat_context(
            self.session, conversation.runtime_snapshot_id, user_id
        )
        history = await conversations.list_messages(user_id=user_id, session_id=session_id)
        practice = await conversations.get_practice_session(session_id=session_id)
        envelope = {
            "schema_version": binding.input_schema_version,
            "request_id": new_ulid(),
            "purpose": "chat_suggestion",
            "user": {"user_id": user_id},
            "session": {
                "session_id": session_id,
                "runtime_snapshot_id": conversation.runtime_snapshot_id,
                "history": [
                    {
                        "role": message.role,
                        "text": message.text,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in history
                ],
            },
            "persona": context["persona"],
            "ai_configuration": context["ai_configuration"],
            "input": {
                "agent_code": context["agent_code"],
                "category_id": practice.category_id,
            },
        }
        return await self.suggestion_adapter.run(binding, envelope)

    async def send(self, *, user_id: str, session_id: str, text: str,
                   client_key: str | None) -> dict[str, Any]:
        prepared = await self._prepare_turn(
            user_id=user_id, session_id=session_id,
            turn_input={
                "modality": "text",
                "text": text,
                # Flow Parser membaca storage_key/media_id apa pun modality-nya;
                # kirim string kosong untuk text agar tidak KeyError di canvas.
                "media_id": "",
                "storage_key": "",
            },
            client_key=client_key,
        )
        # Simpan pesan user + respons agent dengan media_id bila ada
        result = await self.adapter.run(prepared.binding, prepared.envelope)
        return await self._persist_turn(prepared, result)

    async def send_voice(
        self, *, user_id: str, session_id: str, media_id: str,
        text: str | None, client_key: str | None,
    ) -> dict[str, Any]:
        """Kirim voice message: simpan audio metadata + kirim ke Langflow.

        Media harus sudah finalized dan clean. Teks transkrip wajib sudah
        diisi client (alur 3-hit: STT via flow ``stt`` sebelumnya); Langflow
        menerima teks tersebut dan menghasilkan respons audio via TTS.
        """
        prepared = await self._prepare_voice_turn(
            user_id=user_id, session_id=session_id, media_id=media_id,
            text=text, client_key=client_key,
        )
        result = await self.adapter.run(prepared.binding, prepared.envelope)
        return await self._persist_turn(prepared, result)

    async def send_stream(self, *, user_id: str, session_id: str, text: str,
                          client_key: str | None) -> AsyncIterator[ChatStreamEvent]:
        """Chat turn teks via SSE: event bertahap, persistensi identik send().

        User message disimpan SEBELUM stream dibuka agar retry client dengan
        client_key yang sama tidak pernah memanggil ulang Langflow.
        """
        prepared = await self._prepare_turn(
            user_id=user_id, session_id=session_id,
            turn_input={"modality": "text", "text": text,
                        "media_id": "", "storage_key": ""},
            client_key=client_key,
        )
        async for event in self._run_turn_stream(prepared):
            yield event

    async def send_voice_stream(
        self, *, user_id: str, session_id: str, media_id: str,
        text: str | None, audio_duration_ms: int | None = None,
        client_key: str | None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Voice message via SSE: transcript STT muncul lebih awal dari teks/audio."""
        prepared = await self._prepare_voice_turn(
            user_id=user_id, session_id=session_id, media_id=media_id,
            text=text, audio_duration_ms=audio_duration_ms, client_key=client_key,
        )
        async for event in self._run_turn_stream(prepared):
            yield event

    # --- Shared turn pipeline (sync + stream) --------------------------------

    async def _prepare_turn(
        self, *, user_id: str, session_id: str, turn_input: dict[str, Any],
        client_key: str | None,
    ) -> "_PreparedTurn":
        """Validasi session + bangun envelope chat_turn untuk adapter Langflow."""
        if not self.settings.feature_ai_enabled:
            raise FeatureUnavailableError("AI sedang dinonaktifkan.")
        conversations = ConversationService(self.session, self.settings)
        conversation = await conversations.get_owned_session(
            user_id=user_id, session_id=session_id,
        )
        if conversation.state != "active":
            raise ConflictError("Session sudah tidak aktif.", code="SESSION_CLOSED")
        binding = await resolve_flow(
            self.session, environment=self.settings.app_env, purpose="chat_turn",
        )
        context = await chat_context(self.session, conversation.runtime_snapshot_id, user_id)
        history = await conversations.list_messages(user_id=user_id, session_id=session_id)
        practice = await conversations.get_practice_session(session_id=session_id)
        envelope = {
            "schema_version": binding.input_schema_version,
            "request_id": new_ulid(), "purpose": "chat_turn",
            "user": {"user_id": user_id},
            "session": {
                "session_id": session_id,
                "runtime_snapshot_id": conversation.runtime_snapshot_id,
                "history": [
                    {
                        "role": message.role,
                        "text": message.text,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in history
                ],
            },
            "persona": context["persona"],
            "ai_configuration": context["ai_configuration"],
            "input": {
                "agent_code": context["agent_code"],
                "category_id": practice.category_id,
                **turn_input,
            },
            "policy": {
                "tool_allowlist": list(binding.tool_allowlist),
                "retrieval_scopes": [], "output_schema": binding.output_schema_version,
            },
        }
        if client_key is not None:
            envelope["session"]["client_message_id"] = client_key
        return _PreparedTurn(
            user_id=user_id, session_id=session_id, binding=binding,
            envelope=envelope, practice=practice, turn_input=turn_input,
            client_key=client_key,
        )

    async def _prepare_voice_turn(
        self, *, user_id: str, session_id: str, media_id: str,
        text: str | None, audio_duration_ms: int | None = None,
        client_key: str | None,
    ) -> "_PreparedTurn":
        """_prepare_turn untuk voice: validasi media + transkrip wajib.

        Alur 3-hit client (upload → transcribe → kirim): STT sudah terjadi
        via flow ``stt`` sebelum turn ini, sehingga teks transkrip WAJIB
        dikirim client dan tersimpan final sejak INSERT — tidak ada
        placeholder, tidak ada UPDATE (tabel messages tetap append-only).
        """
        media = await self._get_owned_media(user_id, media_id)
        if media.status != "finalized" or media.scan_state != "clean":
            raise ConflictError(
                "Media belum dapat dipakai (scan/status).",
                code="MEDIA_NOT_READY",
            )
        if media.media_type != "audio":
            raise ConflictError(
                "Hanya media audio yang dapat dikirim sebagai voice message.",
                code="MEDIA_TYPE_INVALID",
            )
        if text is None or not text.strip():
            raise ValidationError(
                "Voice message wajib menyertakan teks transkrip.",
                details=[{"field": "text", "message": "wajib diisi"}],
            )
        return await self._prepare_turn(
            user_id=user_id, session_id=session_id,
            turn_input={
                "modality": "audio",
                "media_id": media_id,
                "storage_key": media.storage_key,
                "text": text,  # Transkrip hasil flow stt (alur 3-hit)
                "audio_duration_ms": audio_duration_ms,
            },
            client_key=client_key,
        )

    async def _persist_turn(
        self, prepared: "_PreparedTurn", result: ChatResult,
    ) -> dict[str, Any]:
        """Simpan pesan user + respons agent; identik untuk sync dan stream.

        Pesan user voice di-link ke media-nya sendiri agar tetap dapat
        diputar ulang dari history.
        """
        conversations = ConversationService(self.session, self.settings)
        turn_input = prepared.turn_input
        user_message = await conversations.append_user_message(
            user_id=prepared.user_id,
            session_id=prepared.session_id,
            text=turn_input["text"],
            client_key=prepared.client_key,
            modality=turn_input["modality"],
            media_id=turn_input.get("media_id") or None,
            audio_duration_ms=turn_input.get("audio_duration_ms"),
        )
        agent_media_id = await self._resolve_media_id_from_result(result)
        agent_message = await conversations.append_agent_message(
            session_id=prepared.session_id,
            owner_user_id=prepared.user_id,
            agent_version_id=prepared.practice.agent_version_id,
            text=result.response_text,
            modality="audio" if result.audio_url else "text",
            media_id=agent_media_id,
            audio_duration_ms=result.audio_duration_ms,
        )
        if agent_media_id is None:
            await self._relocate_agent_audio(
                prepared.session_id, agent_message.id, result
            )
        return {
            "session_id": prepared.session_id,
            "message_id": agent_message.id,
            "user_message_id": user_message.id,
            **result.model_dump(),
        }

    async def _run_turn_stream(
        self, prepared: "_PreparedTurn",
    ) -> AsyncIterator[ChatStreamEvent]:
        """Konsumsi run_stream() adapter + persistensi terkait event.

        Persistensi user message terjadi SEBELUM generator adapter diminta
        frame pertama (langsung setelah event ``started`` diterima), sehingga
        retry client dengan client_key yang sama terdeteksi sebelum ada
        pemanggilan ulang provider. Teks pesan user voice adalah transkrip
        final dari flow ``stt`` (alur 3-hit), tersimpan sejak INSERT.
        """
        conversations = ConversationService(self.session, self.settings)
        user_message = None
        agent_persisted = False
        upstream = self.adapter.run_stream(prepared.binding, prepared.envelope)
        # Tarik event pertama (started) SEBELUM persistensi user message —
        # pemanggilan HTTP ke Langflow baru terjadi saat frame berikutnya
        # diminta (lazy pull), jadi duplikasi client_key terdeteksi dulu.
        first = await anext(upstream, None)
        if first is not None and first.kind == "started":
            try:
                user_message = await conversations.append_user_message(
                    user_id=prepared.user_id,
                    session_id=prepared.session_id,
                    text=prepared.turn_input["text"],
                    client_key=prepared.client_key,
                    modality=prepared.turn_input["modality"],
                    media_id=prepared.turn_input.get("media_id") or None,
                    audio_duration_ms=prepared.turn_input.get("audio_duration_ms"),
                )
            except ConflictError:
                # Replay client_key: Langflow belum dipanggil — jangan
                # mulai turn baru; sinyalkan client ambil history saja.
                yield ChatStreamEvent(
                    kind="failed",
                    error="Pesan dengan client_message_id ini sudah ada.",
                    data={"code": "DUPLICATE_MESSAGE"},
                )
                return
            first.data["user_message_id"] = user_message.id
            yield first
        async for event in upstream:
            if event.kind == "completed" and event.result is not None:
                try:
                    agent_media_id = await self._resolve_media_id_from_result(event.result)
                    agent_message = await conversations.append_agent_message(
                        session_id=prepared.session_id,
                        owner_user_id=prepared.user_id,
                        agent_version_id=prepared.practice.agent_version_id,
                        text=event.result.response_text,
                        modality="audio" if event.result.audio_url else "text",
                        media_id=agent_media_id,
                        audio_duration_ms=event.result.audio_duration_ms,
                    )
                    if agent_media_id is None:
                        await self._relocate_agent_audio(
                            prepared.session_id, agent_message.id, event.result
                        )
                except AppError as exc:
                    logger.warning("persist respons agent gagal: %s", exc.code)
                    yield ChatStreamEvent(
                        kind="failed",
                        error="Respons agent belum dapat disimpan.",
                        data={"code": exc.code},
                    )
                    return
                agent_persisted = True
                event.data.update({
                    "session_id": prepared.session_id,
                    "message_id": agent_message.id,
                    "user_message_id": user_message.id if user_message else None,
                    **event.result.model_dump(),
                })
            yield event
        if not agent_persisted and user_message is not None:
            # Turn gagal di tengah: pesan user sudah commit (audit trail),
            # tidak ada respons agent — client menerima terminal failed.
            logger.info(
                "chat turn stream tanpa completed (session=%s)",
                prepared.session_id,
            )

    async def _get_owned_media(self, user_id: str, media_id: str) -> MediaObject:
        """Ambil media dengan ownership check."""
        from sqlalchemy import select
        media = (
            await self.session.execute(
                select(MediaObject).where(MediaObject.id == media_id)
            )
        ).scalar_one_or_none()
        if media is None or media.owner_user_id != user_id:
            raise NotFoundError("Media tidak ditemukan.")
        return media

    async def _resolve_media_id_from_result(
        self, result: "ChatResult | SessionCreateResult"
    ) -> str | None:
        """Cari media_id dari object_key/bucket yang dikembalikan workflow.

        Workflow TTS mengembalikan object_key + bucket; backend resolve ke
        media_objects.id agar message ter-link ke media. Bila tidak ditemukan,
        return None (message tetap tersimpan tanpa media_id).
        """
        if not result.object_key or not result.bucket:
            return None
        from sqlalchemy import select
        media = (
            await self.session.execute(
                select(MediaObject).where(
                    MediaObject.storage_key == result.object_key,
                )
            )
        ).scalar_one_or_none()
        return media.id if media else None

    async def _relocate_agent_audio(
        self,
        session_id: str,
        message_id: str,
        result: "ChatResult | SessionCreateResult",
    ) -> None:
        """Pindahkan object TTS ke key stabil turunan message_id (best-effort).

        Live response hanya valid sesaat (presigned URL kedaluwarsa); history
        membangun ulang URL dari key stabil ``agent_audio_key_pattern``
        bila bucket public-read. Kegagalan copy tidak menggagalkan chat turn.
        """
        if not result.object_key or not result.bucket:
            return
        if not self.settings.agent_audio_fallback_enabled:
            return
        if result.bucket != self.settings.s3_bucket:
            # Object di bucket lain dari yang backend kenal — jangan disentuh.
            return
        try:
            import asyncio

            import boto3  # type: ignore[import-untyped]
            from botocore.client import Config  # type: ignore[import-untyped]

            target_key = self.settings.agent_audio_key_pattern.format(
                session_id=session_id, message_id=message_id
            )
            if target_key == result.object_key:
                return  # flow sudah menulis di key stabil

            def _copy() -> None:
                client = boto3.client(
                    "s3",
                    endpoint_url=self.settings.s3_endpoint_url,
                    region_name=self.settings.s3_region,
                    aws_access_key_id=self.settings.s3_access_key_id,
                    aws_secret_access_key=self.settings.s3_secret_access_key,
                    config=Config(
                        signature_version="s3v4",
                        s3=(
                            {"addressing_style": "path"}
                            if self.settings.s3_force_path_style
                            else {}
                        ),
                    ),
                )
                client.copy_object(
                    Bucket=self.settings.s3_bucket,
                    Key=target_key,
                    CopySource={
                        "Bucket": self.settings.s3_bucket,
                        "Key": result.object_key,
                    },
                )

            await asyncio.to_thread(_copy)
            self._last_object_key_by_session[session_id] = (
                result.object_key,
                target_key,
            )
        except Exception as exc:  # best-effort: jangan gagalkan chat turn
            logger.warning(
                "Relokasi audio agent gagal (history fallback text): %r", exc
            )
