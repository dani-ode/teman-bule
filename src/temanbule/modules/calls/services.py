"""Call service (Phase 6): admission, lease/fencing, turns, graceful end.

Kontrak (implementation-plan.md Phase 6, realtime-podcast.md, billing-plans.md):
- Admission membuat conversation_session kind='call' + call_sessions dengan
  room unik; consent_version wajib dicatat.
- Lease/fencing: satu lease_owner aktif; fencing_token monotonic; stale
  writer (token lama) ditolak — recovery setelah worker crash aman.
- Turns dengan sequence atomik dan epoch; barge-in menandai interrupted_at,
  tidak menghapus turn.
- End: state machine ending -> ended dengan end_reason; low-balance graceful
  end adalah end_reason eksplisit, bukan drop diam-diam.
- LiveKit worker/media pipeline nyata menunggu DEC-14; service ini pemilik
  state otoritatif.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.calls.models import CallSession, CallTurn
from temanbule.modules.catalog.services import RuntimeSnapshotBuilder
from temanbule.modules.conversations.models import ConversationSession
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

CALL_ENDED = "ended"
CALL_ENDING = "ending"
CALL_ACTIVE = "active"
CALL_ADMITTED = "admitted"
CALL_CREATED = "created"

VALID_END_REASONS = {
    "user_hangup",
    "agent_completed",
    "low_balance",
    "balance_exhausted",
    "error",
    "timeout",
    "admin",
}


class CallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.snapshots = RuntimeSnapshotBuilder(session)

    async def create_call(
        self,
        *,
        user_id: str,
        agent_code: str,
        mode: str,
        consent_version: str,
    ) -> CallSession:
        if mode not in ("voice", "video"):
            raise ValidationError(
                "Mode call tidak valid.",
                details=[{"field": "mode", "message": mode}],
            )
        if not consent_version.strip():
            raise ValidationError("consent_version wajib untuk call.")
        snapshot = await self.snapshots.build_for_user(user_id=user_id, agent_code=agent_code)
        conversation = ConversationSession(
            id=new_ulid(),
            user_id=user_id,
            kind="call",
            state="active",
            runtime_snapshot_id=snapshot.id,
        )
        self.session.add(conversation)
        await self.session.flush()
        call = CallSession(
            session_id=conversation.id,
            mode=mode,
            room_name=f"call-{conversation.id}",
            state=CALL_CREATED,
            consent_version=consent_version,
        )
        self.session.add(call)
        await self.session.flush()
        return call

    async def admit_call(
        self, *, user_id: str, session_id: str, lease_owner: str, lease_seconds: int
    ) -> CallSession:
        """Admission + lease acquisition dengan fencing token monotonic."""
        call = await self._owned_call(user_id, session_id, for_update=True)
        if call.state not in (CALL_CREATED, CALL_ADMITTED):
            raise ConflictError(
                "Call tidak dapat diadmit dari state saat ini.",
                code="CALL_STATE_INVALID",
            )
        if not lease_owner.strip():
            raise ValidationError("lease_owner wajib.")
        call.state = CALL_ADMITTED
        call.lease_owner = lease_owner
        call.fencing_token += 1
        call.lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        await self.session.flush()
        return call

    async def activate_call(
        self, *, user_id: str, session_id: str, fencing_token: int, lease_owner: str
    ) -> CallSession:
        """Aktivasi oleh pemegang lease; fencing token harus cocok."""
        call = await self._owned_call(user_id, session_id, for_update=True)
        self._assert_fencing(call, fencing_token, lease_owner)
        if call.state != CALL_ADMITTED:
            raise ConflictError(
                "Call belum diadmit.",
                code="CALL_STATE_INVALID",
            )
        call.state = CALL_ACTIVE
        await self.session.flush()
        return call

    async def record_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        fencing_token: int,
        lease_owner: str,
        speaker: str,
        epoch: int,
    ) -> CallTurn:
        """Catat turn baru; stale lease/fencing ditolak."""
        if speaker not in ("user", "agent"):
            raise ValidationError(
                "Speaker tidak valid.",
                details=[{"field": "speaker", "message": speaker}],
            )
        call = await self._owned_call(user_id, session_id, for_update=True)
        self._assert_fencing(call, fencing_token, lease_owner)
        if call.state != CALL_ACTIVE:
            raise ConflictError(
                "Call tidak aktif.",
                code="CALL_STATE_INVALID",
            )
        sequence = (
            await self.session.execute(
                select(func.coalesce(func.max(CallTurn.sequence), 0)).where(
                    CallTurn.session_id == session_id
                )
            )
        ).scalar_one() + 1
        turn = CallTurn(
            id=new_ulid(),
            session_id=session_id,
            sequence=sequence,
            speaker=speaker,
            epoch=epoch,
            started_at=datetime.now(UTC),
        )
        self.session.add(turn)
        await self.session.flush()
        return turn

    async def interrupt_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str,
        fencing_token: int,
        lease_owner: str,
    ) -> CallTurn:
        """Barge-in: tandai interrupted; tidak menghapus turn (no stale playback)."""
        call = await self._owned_call(user_id, session_id, for_update=True)
        self._assert_fencing(call, fencing_token, lease_owner)
        turn = (
            await self.session.execute(
                select(CallTurn).where(
                    CallTurn.id == turn_id, CallTurn.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        if turn is None:
            raise NotFoundError("Turn tidak ditemukan.")
        if turn.state == "started":
            turn.state = "interrupted"
            turn.interrupted_at = datetime.now(UTC)
            await self.session.flush()
        return turn

    async def end_call(
        self, *, user_id: str, session_id: str, end_reason: str
    ) -> CallSession:
        """Graceful end; idempoten. end_reason eksplisit (low_balance dll)."""
        if end_reason not in VALID_END_REASONS:
            raise ValidationError(
                "end_reason tidak valid.",
                details=[{"field": "end_reason", "message": end_reason}],
            )
        call = await self._owned_call(user_id, session_id, for_update=True)
        if call.state == CALL_ENDED:
            return call
        call.state = CALL_ENDED
        call.end_reason = end_reason
        call.lease_owner = None
        call.lease_until = None
        # Akhiri juga conversation session induk
        conversation = (
            await self.session.execute(
                select(ConversationSession).where(ConversationSession.id == session_id)
            )
        ).scalar_one_or_none()
        if conversation is not None and conversation.state == "active":
            conversation.state = "completed"
            conversation.ended_at = datetime.now(UTC)
        await self.session.flush()
        return call

    async def get_call(self, *, user_id: str, session_id: str) -> CallSession:
        return await self._owned_call(user_id, session_id)

    def _assert_fencing(self, call: CallSession, fencing_token: int, lease_owner: str) -> None:
        """Stale writer (token lama / owner salah) ditolak — crash-safe."""
        if call.lease_owner is None:
            raise ConflictError(
                "Call tidak memiliki lease aktif.",
                code="CALL_NO_LEASE",
            )
        if call.lease_owner != lease_owner or call.fencing_token != fencing_token:
            raise ConflictError(
                "Fencing token/lease owner tidak cocok (stale writer).",
                code="CALL_FENCING_MISMATCH",
            )
        if call.lease_until is not None and call.lease_until <= datetime.now(UTC):
            raise ConflictError(
                "Lease sudah kedaluwarsa.",
                code="CALL_LEASE_EXPIRED",
            )

    async def _owned_call(
        self, user_id: str, session_id: str, *, for_update: bool = False
    ) -> CallSession:
        stmt = (
            select(CallSession)
            .join(ConversationSession, CallSession.session_id == ConversationSession.id)
            .where(CallSession.session_id == session_id)
        )
        if for_update:
            stmt = stmt.with_for_update()
        call = (await self.session.execute(stmt)).scalar_one_or_none()
        if call is None:
            raise NotFoundError("Call tidak ditemukan.")
        conversation = (
            await self.session.execute(
                select(ConversationSession).where(ConversationSession.id == session_id)
            )
        ).scalar_one()
        if conversation.user_id != user_id:
            raise NotFoundError("Call tidak ditemukan.")
        return call
