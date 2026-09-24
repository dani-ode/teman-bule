"""AI runtime module models: execution grants (FND-09)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base


class ExecutionGrant(Base):
    """Grant berumur pendek; id adalah execution_ref. Bukan bearer credential."""

    __tablename__ = "execution_grants"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # execution_ref (ULID)
    request_id: Mapped[str] = mapped_column(String(26))
    snapshot_id: Mapped[str | None] = mapped_column(String(26))
    service_identity: Mapped[str] = mapped_column(String(64), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(26), index=True)
    resource_ref: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[str] = mapped_column(Text)  # JSON array string
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_policy: Mapped[str] = mapped_column(String(20), default="single_use")

    __table_args__ = (Index("ix_execution_grants_expiry", "expires_at"),)
