"""Call transcript persistence (Phase 6).

Turn realtime dipetakan ke ``conversation_messages`` append-only
(modality='voice') dengan sequence atomik per session — kontrak history yang
sama dengan chat (postgresql-schema.md). Message asli disimpan SEBELUM
ingestion background (realtime-podcast.md: background setelah interaksi).

Role message mengikuti konvensi ``conversation_messages.role``:
``user`` untuk speaker user, ``agent`` untuk speaker agent.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.models import ConversationMessage
from temanbule.platform.errors import ValidationError
from temanbule.platform.security import new_ulid

_SPEAKER_TO_ROLE = {"user": "user", "agent": "agent"}


async def append_turn_message(
    session: AsyncSession,
    *,
    session_id: str,
    owner_user_id: str,
    speaker: str,
    text: str,
    interrupted: bool,
) -> ConversationMessage:
    """Tambahkan satu message transcript untuk turn; sequence monotonic.

    ``interrupted=True`` menandai ``terminal_state='interrupted'`` — span
    delivered vs generated tetap kontrak sisi worker (DEC-14).
    """
    role = _SPEAKER_TO_ROLE.get(speaker)
    if role is None:
        raise ValidationError(
            "Speaker tidak valid.",
            details=[{"field": "speaker", "message": speaker}],
        )
    if not text.strip():
        raise ValidationError("Text transcript tidak boleh kosong.")
    sequence = (
        await session.execute(
            select(func.coalesce(func.max(ConversationMessage.sequence), 0)).where(
                ConversationMessage.session_id == session_id
            )
        )
    ).scalar_one() + 1
    message = ConversationMessage(
        id=new_ulid(),
        session_id=session_id,
        owner_user_id=owner_user_id,
        role=role,
        modality="voice",
        text=text,
        sequence=sequence,
        terminal_state="interrupted" if interrupted else "completed",
    )
    session.add(message)
    await session.flush()
    return message
