"""Connect the authenticated chat route to its PostgreSQL-registered workflow."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.chat_context import chat_context
from temanbule.modules.catalog.flows import resolve_flow
from temanbule.modules.conversations.langflow import LangflowChatAdapter
from temanbule.modules.conversations.services import ConversationService
from temanbule.platform.errors import ConflictError, FeatureUnavailableError
from temanbule.platform.security import new_ulid
from temanbule.platform.settings import Settings


class ChatService:
    def __init__(self, session: AsyncSession, settings: Settings,
                 adapter: LangflowChatAdapter | None = None):
        self.session = session
        self.settings = settings
        self.adapter = adapter or LangflowChatAdapter(settings)

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
