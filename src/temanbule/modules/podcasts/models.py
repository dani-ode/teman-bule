"""Podcasts module models (Phase 7). Script ready immutable via trigger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base, TimestampMixin, utcnow


class Podcast(Base, TimestampMixin):
    __tablename__ = "podcasts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="created")
    current_source_version_id: Mapped[str | None] = mapped_column(String(26))
    current_script_version_id: Mapped[str | None] = mapped_column(String(26))
    generation_job_id: Mapped[str | None] = mapped_column(String(26))


class PodcastSourceVersion(Base):
    __tablename__ = "podcast_source_versions"
    __table_args__ = (
        UniqueConstraint("podcast_id", "revision", name="uq_podcast_source_revision"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    podcast_id: Mapped[str] = mapped_column(
        ForeignKey("podcasts.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_objects.id", ondelete="RESTRICT"))
    checksum: Mapped[str] = mapped_column(String(64))
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PodcastScriptVersion(Base):
    __tablename__ = "podcast_script_versions"
    __table_args__ = (
        UniqueConstraint("podcast_id", "revision", name="uq_podcast_script_revision"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    podcast_id: Mapped[str] = mapped_column(
        ForeignKey("podcasts.id", ondelete="CASCADE"), index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("podcast_source_versions.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer)
    runtime_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_snapshots.id", ondelete="RESTRICT")
    )
    outline: Mapped[str] = mapped_column(Text)
    target_duration_seconds: Mapped[int] = mapped_column(Integer)
    estimated_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PodcastSegment(Base):
    __tablename__ = "podcast_segments"
    __table_args__ = (
        UniqueConstraint("script_version_id", "position", name="uq_podcast_segments_position"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    script_version_id: Mapped[str] = mapped_column(
        ForeignKey("podcast_script_versions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    agent_version_id: Mapped[str] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    text: Mapped[str] = mapped_column(Text)
    citations: Mapped[str | None] = mapped_column(Text)
    estimated_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PodcastPlayback(Base, TimestampMixin):
    __tablename__ = "podcast_playbacks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    podcast_id: Mapped[str] = mapped_column(
        ForeignKey("podcasts.id", ondelete="CASCADE"), index=True
    )
    script_version_id: Mapped[str] = mapped_column(
        ForeignKey("podcast_script_versions.id", ondelete="RESTRICT")
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="RESTRICT"), unique=True
    )
    room_name: Mapped[str] = mapped_column(String(128), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="created")
    segment_cursor: Mapped[int] = mapped_column(Integer, default=0)
    offset_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    branch_ref: Mapped[str | None] = mapped_column(String(80))
    epoch: Mapped[int] = mapped_column(BigInteger, default=0)
    elapsed_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(40))


class PodcastAudioCache(Base):
    __tablename__ = "podcast_audio_cache"
    __table_args__ = (
        UniqueConstraint("segment_id", "voice_config_hash", name="uq_podcast_audio_cache_segment"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    segment_id: Mapped[str] = mapped_column(ForeignKey("podcast_segments.id", ondelete="CASCADE"))
    voice_config_hash: Mapped[str] = mapped_column(String(64))
    media_id: Mapped[str] = mapped_column(ForeignKey("media_objects.id", ondelete="RESTRICT"))
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
