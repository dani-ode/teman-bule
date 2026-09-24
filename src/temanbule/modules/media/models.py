"""Media module models (Phase 5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base, TimestampMixin


class MediaObject(Base, TimestampMixin):
    __tablename__ = "media_objects"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    media_type: Mapped[str] = mapped_column(String(40))  # audio | image | pdf
    bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending_upload", index=True)
    scan_state: Mapped[str] = mapped_column(String(20), default="pending")
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
