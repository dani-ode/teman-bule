"""Conversation service (Phase 3): practice sessions + messages.

Kontrak:
- Session dibuat dengan runtime snapshot (plan/agent terverifikasi backend).
- Pesan user disimpan SEBELUM invocation AI; sequence atomik per session.
- client_key (client_message_id) idempoten per session: replay → pesan sama.
- Ownership ketat: session/pesan privat, cross-owner = 404 (bukan 403 bocor).
- Agent reply disimpan dengan agent_version_id dan terminal_state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import Agent, AgentVersion
from temanbule.modules.catalog.services import RuntimeSnapshotBuilder
from temanbule.modules.conversations.models import (
    ConversationMessage,
    ConversationSession,
    PracticeCategory,
    PracticeSession,
)
from temanbule.modules.reliability.outbox import record_outbox_event
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

SESSION_ACTIVE = "active"
SESSION_COMPLETED = "completed"

ROLE_USER = "user"
ROLE_AGENT = "agent"


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.snapshots = RuntimeSnapshotBuilder(session)

    async def start_practice_session(
        self, *, user_id: str, agent_code: str, category_id: str
    ) -> ConversationSession:
        category = (
            await self.session.execute(
                select(PracticeCategory).where(
                    PracticeCategory.id == category_id,
                    PracticeCategory.status == "published",
                )
            )
        ).scalar_one_or_none()
        if category is None:
            raise NotFoundError("Kategori practice tidak tersedia.")

        snapshot = await self.snapshots.build_for_user(user_id=user_id, agent_code=agent_code)
        conversation = ConversationSession(
            id=new_ulid(),
            user_id=user_id,
            kind="chat",
            state=SESSION_ACTIVE,
            runtime_snapshot_id=snapshot.id,
        )
        self.session.add(conversation)
        await self.session.flush()
        agent_version_id = snapshot.agent_version_id
        if agent_version_id is None:
            raise ConflictError(
                "Snapshot tanpa agent version.",
                code="SNAPSHOT_INVALID",
            )
        self.session.add(
            PracticeSession(
                session_id=conversation.id,
                category_id=category.id,
                agent_version_id=agent_version_id,
            )
        )
        await self.session.flush()
        return conversation

    async def get_practice_session(self, *, session_id: str) -> PracticeSession:
        """Ambil baris practice_sessions (category_id + agent_version_id) per session."""
        practice = (
            await self.session.execute(
                select(PracticeSession).where(PracticeSession.session_id == session_id)
            )
        ).scalar_one_or_none()
        if practice is None:
            raise NotFoundError("Practice session tidak ditemukan.")
        return practice

    async def get_agent_code_for_session(self, *, session_id: str) -> str:
        """Resolve agent_code via practice_sessions → agent_versions → agents."""
        code = (
            await self.session.execute(
                select(Agent.code)
                .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                .join(PracticeSession, PracticeSession.agent_version_id == AgentVersion.id)
                .where(PracticeSession.session_id == session_id)
            )
        ).scalar_one_or_none()
        if code is None:
            raise NotFoundError("Agent session tidak ditemukan.")
        return code

    async def append_user_message(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
        client_key: str | None,
    ) -> ConversationMessage:
        """Simpan pesan user; idempoten per client_key dalam session."""
        if not text.strip():
            raise ValidationError("Pesan kosong.")
        conversation = await self._owned_session(user_id, session_id)
        if conversation.state != SESSION_ACTIVE:
            raise ConflictError(
                "Session sudah tidak aktif.",
                code="SESSION_CLOSED",
            )

        if client_key is not None:
            existing = (
                await self.session.execute(
                    select(ConversationMessage).where(
                        ConversationMessage.session_id == session_id,
                        ConversationMessage.client_key == client_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        message = ConversationMessage(
            id=new_ulid(),
            session_id=session_id,
            owner_user_id=user_id,
            role=ROLE_USER,
            text=text,
            sequence=await self._next_sequence(session_id),
            client_key=client_key,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def append_agent_message(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        agent_version_id: str,
        text: str,
        terminal_state: str = "completed",
        modality: str = "text",
    ) -> ConversationMessage:
        """Simpan respons agent setelah invocation terverifikasi."""
        message = ConversationMessage(
            id=new_ulid(),
            session_id=session_id,
            owner_user_id=owner_user_id,
            role=ROLE_AGENT,
            agent_version_id=agent_version_id,
            text=text,
            modality=modality,
            sequence=await self._next_sequence(session_id),
            terminal_state=terminal_state,
            generated_at=datetime.now(UTC),
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def complete_session(self, *, user_id: str, session_id: str) -> ConversationSession:
        conversation = await self._owned_session(user_id, session_id, for_update=True)
        if conversation.state == SESSION_COMPLETED:
            return conversation
        conversation.state = SESSION_COMPLETED
        conversation.ended_at = datetime.now(UTC)
        await self.session.flush()

        # Outbox atomik dengan domain mutation (backend-layout.md aturan 5).
        # Payload minimal reference — tanpa isi pesan mentah (api-events.md).
        message_count = (
            await self.session.execute(
                select(func.count(ConversationMessage.id)).where(
                    ConversationMessage.session_id == session_id
                )
            )
        ).scalar_one()
        record_outbox_event(
            self.session,
            aggregate_type="conversation_session",
            aggregate_id=session_id,
            aggregate_version=await self._next_aggregate_version(session_id),
            event_type="conversation.session_completed.v1",
            payload={
                "session_id": session_id,
                "owner_user_id": user_id,
                "kind": conversation.kind,
                "message_count": message_count,
            },
        )
        await self.session.flush()
        return conversation

    async def _next_aggregate_version(self, session_id: str) -> int:
        """Aggregate version monotonic untuk ordering event per session."""
        from temanbule.modules.reliability.models import OutboxEvent

        current = (
            await self.session.execute(
                select(func.coalesce(func.max(OutboxEvent.aggregate_version), 0)).where(
                    OutboxEvent.aggregate_type == "conversation_session",
                    OutboxEvent.aggregate_id == session_id,
                )
            )
        ).scalar_one()
        return current + 1

    async def list_messages(
        self, *, user_id: str, session_id: str, limit: int = 50
    ) -> list[ConversationMessage]:
        await self._owned_session(user_id, session_id)
        rows = (
            (
                await self.session.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == session_id)
                    .order_by(ConversationMessage.sequence.asc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def get_owned_session(self, *, user_id: str, session_id: str) -> ConversationSession:
        return await self._owned_session(user_id, session_id)

    async def delete_session(self, *, user_id: str, session_id: str) -> None:
        """Hapus session beserta seluruh pesan terkait (CASCADE).

        Ownership ketat: session privat, cross-owner = 404 (bukan 403 bocor).
        Menghapus session juga menghapus messages via FK CASCADE.
        """
        conversation = await self._owned_session(user_id, session_id, for_update=True)
        await self.session.execute(
            delete(ConversationMessage).where(
                ConversationMessage.session_id == session_id
            )
        )
        await self.session.execute(
            delete(PracticeSession).where(
                PracticeSession.session_id == session_id
            )
        )
        await self.session.execute(
            delete(ConversationSession).where(
                ConversationSession.id == conversation.id
            )
        )
        await self.session.flush()

    async def list_practice_sessions(
        self,
        *,
        user_id: str,
        category_id: str | None = None,
        agent_code: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[tuple[ConversationSession, str, str]]:
        """Daftar session chat milik user; filter kategori/agent/state opsional.

        Mengembalikan tuple (session, category_id, agent_code) agar klien dapat
        menampilkan konteks tanpa query tambahan per baris. Agent code di-resolve
        via join agent_versions → agents. Ownership selalu ditegakkan (user_id).
        """
        stmt = (
            select(ConversationSession, PracticeSession.category_id, Agent.code)
            .join(PracticeSession, PracticeSession.session_id == ConversationSession.id)
            .join(AgentVersion, AgentVersion.id == PracticeSession.agent_version_id)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(
                ConversationSession.user_id == user_id,
                ConversationSession.kind == "chat",
            )
            .order_by(ConversationSession.started_at.desc())
            .limit(min(limit, 100))
        )
        if category_id is not None:
            stmt = stmt.where(PracticeSession.category_id == category_id)
        if agent_code is not None:
            stmt = stmt.where(Agent.code == agent_code)
        if state is not None:
            stmt = stmt.where(ConversationSession.state == state)
        rows = (await self.session.execute(stmt)).all()
        return [(conversation, cat_id, code) for conversation, cat_id, code in rows]


    async def _next_sequence(self, session_id: str) -> int:
        """Sequence berikutnya; aman untuk concurrent append dalam transaksi."""
        current = (
            await self.session.execute(
                select(func.coalesce(func.max(ConversationMessage.sequence), 0)).where(
                    ConversationMessage.session_id == session_id
                )
            )
        ).scalar_one()
        return current + 1

    async def _owned_session(
        self, user_id: str, session_id: str, *, for_update: bool = False
    ) -> ConversationSession:
        stmt = select(ConversationSession).where(ConversationSession.id == session_id)
        if for_update:
            stmt = stmt.with_for_update()
        conversation = (await self.session.execute(stmt)).scalar_one_or_none()
        # Cross-owner dan missing sama-sama 404: tidak membocorkan eksistensi.
        if conversation is None or conversation.user_id != user_id:
            raise NotFoundError("Session tidak ditemukan.")
        return conversation
