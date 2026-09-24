"""Calls module models (Phase 6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base, TimestampMixin, utcnow


class CallSession(Base, TimestampMixin):
    __tablename__ = "call_sessions"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(10))  # voice | video
    room_name: Mapped[str] = mapped_column(String(128), unique=True)
    room_sid: Mapped[str | None] = mapped_column(String(128), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="created")
    end_reason: Mapped[str | None] = mapped_column(String(40))
    consent_version: Mapped[str] = mapped_column(String(40))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallTurn(Base):
    __tablename__ = "call_turns"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_call_turns_sequence"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("call_sessions.session_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))  # user | agent
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(20), default="started")
    epoch: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
