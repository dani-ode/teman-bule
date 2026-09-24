"""Reliability repository: idempotency, outbox, jobs, audit, bootstrap."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.reliability.models import (
    AuditEvent,
    BackgroundJob,
    BootstrapRun,
    IdempotencyRecord,
    OutboxEvent,
)


class ReliabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Idempotency ---
    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        self.session.add(record)

    async def get_idempotency_record(
        self, *, principal: str, operation: str, key: str
    ) -> IdempotencyRecord | None:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.principal == principal,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # --- Outbox ---
    def add_outbox_event(self, event: OutboxEvent) -> None:
        self.session.add(event)

    async def get_unpublished_outbox_events(self, *, limit: int = 100) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.occurred_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_outbox_published(self, event_id: str) -> None:
        event = await self.session.get(OutboxEvent, event_id)
        if event is not None:
            event.published_at = datetime.now(UTC)

    # --- Jobs ---
    def add_job(self, job: BackgroundJob) -> None:
        self.session.add(job)

    async def get_job_by_dedupe_key(self, dedupe_key: str) -> BackgroundJob | None:
        stmt = select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_job(self, job_id: str) -> BackgroundJob | None:
        return await self.session.get(BackgroundJob, job_id)

    # --- Audit ---
    def add_audit_event(self, event: AuditEvent) -> None:
        self.session.add(event)

    # --- Bootstrap ---
    def add_bootstrap_run(self, run: BootstrapRun) -> None:
        self.session.add(run)

    async def get_bootstrap_runs(
        self, *, environment: str, manifest_version: str
    ) -> list[BootstrapRun]:
        stmt = (
            select(BootstrapRun)
            .where(
                BootstrapRun.environment == environment,
                BootstrapRun.manifest_version == manifest_version,
            )
            .order_by(BootstrapRun.attempt_number.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_max_bootstrap_attempt(self, *, environment: str, manifest_version: str) -> int:
        stmt = select(func.max(BootstrapRun.attempt_number)).where(
            BootstrapRun.environment == environment,
            BootstrapRun.manifest_version == manifest_version,
        )
        result = await self.session.execute(stmt)
        value = result.scalar_one_or_none()
        return int(value) if value is not None else 0
