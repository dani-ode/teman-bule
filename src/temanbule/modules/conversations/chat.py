"""Connect the authenticated chat route to its PostgreSQL-registered workflow."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.chat_context import chat_context
from temanbule.modules.catalog.flows import resolve_flow
from temanbule.modules.conversations.langflow import LangflowChatAdapter
from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.conversations.session_create import LangflowSessionCreateAdapter
from temanbule.modules.media.models import MediaObject
from temanbule.platform.errors import (
    AppError,
    ConflictError,
    FeatureUnavailableError,
    NotFoundError,
)
from temanbule.platform.security import new_ulid
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, session: AsyncSession, settings: Settings,
                 adapter: LangflowChatAdapter | None = None,
                 session_create_adapter: LangflowSessionCreateAdapter | None = None):
        self.session = session
        self.settings = settings
        self.adapter = adapter or LangflowChatAdapter(settings)
        self.session_create_adapter = session_create_adapter or LangflowSessionCreateAdapter(
            settings
        )

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
        conversations = ConversationService(self.session)
        conversation = await conversations.start_practice_session(
            user_id=user_id, agent_code=agent_code, category_id=category_id,
        )
        practice = await conversations.get_practice_session(session_id=conversation.id)

        first_message: dict[str, Any] | None = None
        try:
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
                "input": {"agent_code": agent_code, "category_id": category_id},
            }
            result = await self.session_create_adapter.run(binding, envelope)
            message = await conversations.append_agent_message(
                session_id=conversation.id,
                owner_user_id=user_id,
                agent_version_id=practice.agent_version_id,
                text=result.response_text,
                # Chat mostly audio: greeting disimpan sebagai modality 'audio'
                # bila flow mengembalikan URL audio S3/MinIO hasil TTS.
                modality="audio" if result.audio_url else "text",
            )
            first_message = {
                "message_id": message.id,
                "session_id": message.session_id,
                "role": message.role,
                "text": message.text,
                "modality": message.modality,
                "audio_url": result.audio_url,
                "audio_duration_ms": result.audio_duration_ms,
                "sequence": message.sequence,
                "terminal_state": message.terminal_state,
                "created_at": message.created_at.isoformat(),
            }
        except AppError as exc:
            await self.session.rollback()
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

    async def send(self, *, user_id: str, session_id: str, text: str,
                   client_key: str | None) -> dict[str, Any]:
        if not self.settings.feature_ai_enabled:
            raise FeatureUnavailableError("AI sedang dinonaktifkan.")
        conversations = ConversationService(self.session)
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
        envelope = {
            "schema_version": binding.input_schema_version,
            "request_id": new_ulid(), "purpose": "chat_turn",
            "user": {"user_id": user_id},
            "session": {
                "session_id": session_id,
                "runtime_snapshot_id": conversation.runtime_snapshot_id,
                "history": [{"role": message.role, "text": message.text} for message in history],
            },
            "persona": context["persona"],
            "ai_configuration": context["ai_configuration"],
            "input": {"modality": "text", "text": text},
            "policy": {
                "tool_allowlist": list(binding.tool_allowlist),
                "retrieval_scopes": [], "output_schema": binding.output_schema_version,
            },
        }
        if client_key is not None:
            envelope["session"]["client_message_id"] = client_key
        result = await self.adapter.run(binding, envelope)
        return {"session_id": session_id, **result.model_dump()}

    async def send_voice(
        self, *, user_id: str, session_id: str, media_id: str,
        text: str | None, client_key: str | None,
    ) -> dict[str, Any]:
        """Kirim voice message: simpan audio metadata + kirim ke Langflow.

        Media harus sudah finalized dan clean. Langflow akan melakukan STT
        dan menghasilkan respons audio via TTS.
        """
        if not self.settings.feature_ai_enabled:
            raise FeatureUnavailableError("AI sedang dinonaktifkan.")

        # Validasi session
        conversations = ConversationService(self.session)
        conversation = await conversations.get_owned_session(
            user_id=user_id, session_id=session_id,
        )
        if conversation.state != "active":
            raise ConflictError("Session sudah tidak aktif.", code="SESSION_CLOSED")

        # Validasi media ownership dan status
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

        # Build Langflow envelope dengan audio
        binding = await resolve_flow(
            self.session, environment=self.settings.app_env, purpose="chat_turn",
        )
        context = await chat_context(self.session, conversation.runtime_snapshot_id, user_id)
        history = await conversations.list_messages(user_id=user_id, session_id=session_id)

        envelope = {
            "schema_version": binding.input_schema_version,
            "request_id": new_ulid(), "purpose": "chat_turn",
            "user": {"user_id": user_id},
            "session": {
                "session_id": session_id,
                "runtime_snapshot_id": conversation.runtime_snapshot_id,
                "history": [{"role": message.role, "text": message.text} for message in history],
            },
            "persona": context["persona"],
            "ai_configuration": context["ai_configuration"],
            "input": {
                "modality": "audio",
                "media_id": media_id,
                "storage_key": media.storage_key,
                "text": text,  # Optional: pre-transcribed text atau hint
            },
            "policy": {
                "tool_allowlist": list(binding.tool_allowlist),
                "retrieval_scopes": [], "output_schema": binding.output_schema_version,
            },
        }
        if client_key is not None:
            envelope["session"]["client_message_id"] = client_key

        result = await self.adapter.run(binding, envelope)
        return {"session_id": session_id, **result.model_dump()}

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
