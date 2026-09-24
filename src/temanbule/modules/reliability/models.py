"""Reliability module models: idempotency, outbox, jobs, audit, bootstrap (FND-03/04/05)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from temanbule.platform.base import Base


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    principal: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("principal", "operation", "key", name="uq_idempotency_principal_op_key"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    aggregate_version: Mapped[int] = mapped_column(BigInteger, default=0)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[str] = mapped_column(Text)  # JSON teredaksi, tanpa secret
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (Index("ix_outbox_unpublished", "published_at"),)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    outbox_ref: Mapped[str | None] = mapped_column(String(26))
    purpose: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    checkpoint: Mapped[str | None] = mapped_column(Text)
    safe_error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BackgroundJobAttempt(Base):
    __tablename__ = "background_job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    execution_grant_id: Mapped[str | None] = mapped_column(String(26))
    runtime_snapshot_id: Mapped[str | None] = mapped_column(String(26))
    vendor_job_id: Mapped[str | None] = mapped_column(String(128))
    vendor: Mapped[str | None] = mapped_column(String(40))
    vendor_version: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[str | None] = mapped_column(Text)
    safe_error: Mapped[str | None] = mapped_column(Text)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint("user_id", "tool_name", "idempotency_key", name="uq_tool_exec"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    context_refs: Mapped[str | None] = mapped_column(Text)
    result_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only; tidak ada update/delete."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128))  # user_id | service name
    action: Mapped[str] = mapped_column(String(128))
    target: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str] = mapped_column(String(20))  # success|denied|failure
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), index=True)
    scope: Mapped[str] = mapped_column(String(64))
    tombstone_version: Mapped[int] = mapped_column(BigInteger, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    progress: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BootstrapRun(Base):
    __tablename__ = "bootstrap_runs"
    __table_args__ = (
        UniqueConstraint(
            "environment", "manifest_version", "attempt_number", name="uq_bootstrap_run"
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    environment: Mapped[str] = mapped_column(String(40))
    manifest_version: Mapped[str] = mapped_column(String(40))
    checksum: Mapped[str] = mapped_column(String(64))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    actor_ref: Mapped[str | None] = mapped_column(String(128))
    migration_revision: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_summary: Mapped[str | None] = mapped_column(Text)
    safe_error: Mapped[str | None] = mapped_column(Text)
