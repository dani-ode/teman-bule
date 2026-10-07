"""Background job runner (Phase 3+): claim/execute/retry durable jobs.

Kontrak (architecture.md, execution-readiness.md, postgresql-schema.md):
- SQL job adalah pemilik retry/completion; processor vendor (Langflow,
  embedding provider) hanya mengolah data turunan lewat adapter port.
- Claim memakai lease + fencing token: UPDATE ... WHERE state='pending'
  AND run_after<=now AND (locked_until IS NULL OR locked_until<now)
  mengembalikan satu job; fencing_token naik setiap claim sehingga dua worker
  tidak menjalankan job sama (stale claimant terdeteksi saat finalize).
- Satu attempt dicatat di background_job_attempts per eksekusi.
- Kegagalan vendor (DependencyUnavailableError) → state kembali 'pending'
  dengan run_after backoff; kegagalan permanen (ValidationError/NotFoundError)
  → 'failed'. attempt_count >= max_attempts → 'failed' (dead letter).
- Tidak ada fake success: processor gagal → job tetap nonterminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from temanbule.modules.conversations.models import ConversationMessage
from temanbule.modules.knowledge.embedding_adapter import (
    DualEmbeddingAdapter,
    resolve_embedding_profile,
)
from temanbule.modules.knowledge.langflow_adapter import LangflowExtractionAdapter
from temanbule.modules.knowledge.services import (
    JOB_PURPOSE_EMBEDDING,
    JOB_PURPOSE_INGESTION,
    IngestionService,
    KnowledgeService,
)
from temanbule.modules.reliability.models import BackgroundJob, BackgroundJobAttempt
from temanbule.platform.errors import DependencyUnavailableError, ValidationError
from temanbule.platform.security import new_ulid
from temanbule.platform.settings import Settings
from temanbule.worker.handlers import (
    JOB_PURPOSE_ASSESSMENT,
    JOB_PURPOSE_FACT_EXTRACTION,
    JOB_PURPOSE_PODCAST_INGESTION,
    PODCAST_INGESTION_MAX_POLLS,
    PODCAST_INGESTION_POLL_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

JOB_STATE_PENDING = "pending"
JOB_STATE_RUNNING = "running"
JOB_STATE_SUCCEEDED = "succeeded"
JOB_STATE_FAILED = "failed"

_RETRY_BASE_DELAY_SECONDS = 5.0


class JobRunner:
    """Mengeksekusi BackgroundJob antrian SQL dengan lease + fencing.

    Satu instance dipakai oleh satu worker process. Adapter vendor diinjeksikan
    terkonfigurasi dari settings; tidak ada credential dari payload job.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        extractor: LangflowExtractionAdapter | None,
        embedder: DualEmbeddingAdapter | None,
        podcast_adapter: Any | None = None,
        lease_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._extractor = extractor
        self._embedder = embedder
        self._podcast_adapter = podcast_adapter
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def run_once(self, *, batch: int = 5) -> int:
        """Claim + eksekusi sampai ``batch`` job; return jumlah yang diproses."""
        processed = 0
        for _ in range(batch):
            claimed = await self._claim_one()
            if claimed is None:
                break
            await self._execute(claimed)
            processed += 1
        return processed

    # --- Claim ------------------------------------------------------------

    async def _claim_one(self) -> BackgroundJob | None:
        """Claim satu job due; fencing_token naik untuk mendeteksi stale worker."""
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            stmt = (
                select(BackgroundJob)
                .where(
                    BackgroundJob.state == JOB_STATE_PENDING,
                    BackgroundJob.run_after <= now,
                )
                .where(
                    (BackgroundJob.locked_until.is_(None))
                    | (BackgroundJob.locked_until < now)
                )
                .order_by(BackgroundJob.run_after.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                return None
            job.state = JOB_STATE_RUNNING
            job.fencing_token += 1
            # attempt_count dinaikkan SAAT claim agar attempt_number unik meski
            # finalize sebelumnya gagal (mencegah pelanggaran uq_job_attempt
            # pada re-claim) dan dead-letter dapat diputus dari nilai ini.
            job.attempt_count += 1
            job.locked_until = now + timedelta(seconds=self._lease_seconds)
            await session.commit()
            await session.refresh(job)
            # Detach agar aman dipakai lintas session saat eksekusi.
            session.expunge(job)
            return job

    # --- Execute ----------------------------------------------------------

    async def _execute(self, claimed: BackgroundJob) -> None:
        """Jalankan processor sesuai purpose; finalize state di akhir."""
        started = datetime.now(UTC)
        attempt = BackgroundJobAttempt(
            id=new_ulid(),
            job_id=claimed.id,
            attempt_number=claimed.attempt_count,  # sudah naik saat claim
            state=JOB_STATE_RUNNING,
            started_at=started,
            deadline_at=started + timedelta(seconds=self._lease_seconds),
        )
        safe_error: str | None = None
        outcome = JOB_STATE_SUCCEEDED
        try:
            await self._dispatch(claimed)
        except DependencyUnavailableError as exc:
            # Transient: jadwalkan ulang; bukan terminal.
            outcome = JOB_STATE_PENDING
            safe_error = exc.code
        except Exception as exc:  # permanent: validation/notfound/state
            outcome = JOB_STATE_FAILED
            safe_error = type(exc).__name__
            logger.exception(
                "job_permanent_failure",
                exc_info=exc,
                extra={"job_id": claimed.id, "purpose": claimed.purpose},
            )

        await self._finalize(
            claimed=claimed,
            attempt=attempt,
            outcome=outcome,
            safe_error=safe_error,
            ended=datetime.now(UTC),
        )

    async def _dispatch(self, job: BackgroundJob) -> None:
        """Routing per purpose ke processor nyata. Tidak ada mock."""
        payload = json.loads(job.payload)
        async with self._session_factory() as session:
            if job.purpose == JOB_PURPOSE_INGESTION:
                if self._extractor is None:
                    raise DependencyUnavailableError(
                        "Extraction adapter belum terkonfigurasi.",
                        code="EXTRACTION_ADAPTER_MISSING",
                    )
                await IngestionService(session).run_ingestion(
                    job_id=job.id,
                    session_id=payload["session_id"],
                    owner_user_id=payload["owner_user_id"],
                    source_start=payload["source_start"],
                    source_end=payload["source_end"],
                    flow_version=payload["flow_version"],
                    extractor=self._extractor,
                )
            elif job.purpose == JOB_PURPOSE_EMBEDDING:
                if self._embedder is None:
                    raise DependencyUnavailableError(
                        "Embedding adapter belum terkonfigurasi.",
                        code="EMBEDDING_ADAPTER_MISSING",
                    )
                spec = await resolve_embedding_profile(
                    session,
                    profile_id=payload["profile_id"],
                    environment=self._settings.app_env,
                    scope=payload["scope"],
                )
                await KnowledgeService(session).run_embedding_job(
                    job=job, embedder=_SpecEmbeddingBridge(self._embedder, spec)
                )
            elif job.purpose in (JOB_PURPOSE_FACT_EXTRACTION, JOB_PURPOSE_ASSESSMENT):
                if self._extractor is None:
                    raise DependencyUnavailableError(
                        "Extraction adapter belum terkonfigurasi.",
                        code="EXTRACTION_ADAPTER_MISSING",
                    )
                await self._run_analysis(
                    session=session,
                    purpose=job.purpose,
                    payload=payload,
                )
            elif job.purpose == JOB_PURPOSE_PODCAST_INGESTION:
                if self._podcast_adapter is None:
                    raise DependencyUnavailableError(
                        "Podcast adapter belum terkonfigurasi.",
                        code="PODCAST_ADAPTER_MISSING",
                    )
                await self._run_podcast_ingestion(session=session, job=job, payload=payload)
            else:
                raise DependencyUnavailableError(
                    f"Purpose job tidak dikenal: {job.purpose}",
                    code="JOB_PURPOSE_UNKNOWN",
                )
            await session.commit()

    async def _run_analysis(
        self,
        *,
        session: AsyncSession,
        purpose: str,
        payload: dict[str, Any],
    ) -> None:
        """Proses fact extraction / learning assessment via flow Langflow.

        Memanggil flow dengan input dari extraction payload, lalu persist hasil
        lewat domain service (idempoten). Tidak ada fake success.
        """
        if self._extractor is None:  # guard: dijamin pemanggil, untuk type narrowing
            raise DependencyUnavailableError(
                "Extraction adapter belum terkonfigurasi.",
                code="EXTRACTION_ADAPTER_MISSING",
            )
        session_id = payload["session_id"]
        owner_user_id = payload["owner_user_id"]
        source_start = payload["source_start"]
        source_end = payload["source_end"]
        flow_version = payload["flow_version"]

        # Ambil pesan sumber sebagai konteks untuk flow analisis.
        messages = (
            (
                await session.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.session_id == session_id,
                        ConversationMessage.sequence >= source_start,
                        ConversationMessage.sequence <= source_end,
                    )
                    .order_by(ConversationMessage.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        message_payload = [
            {
                "sequence": m.sequence,
                "role": m.role,
                "modality": m.modality,
                "text": m.text,
            }
            for m in messages
        ]
        input_data = {
            "schema_version": flow_version,
            "flow_version": flow_version,
            "session_id": session_id,
            "messages": message_payload,
        }
        data = await self._extractor.run_purpose_flow(
            purpose=purpose,
            input_data=input_data,
            session_id=session_id,
        )

        if purpose == JOB_PURPOSE_FACT_EXTRACTION:
            await self._persist_facts(
                session=session,
                owner_user_id=owner_user_id,
                data=data,
                source_version=f"{session_id}:{source_start}:{source_end}",
            )
        else:
            await self._persist_assessment(
                session=session,
                owner_user_id=owner_user_id,
                session_id=session_id,
                source_start=source_start,
                source_end=source_end,
                flow_version=flow_version,
                data=data,
            )

    async def _persist_facts(
        self,
        *,
        session: AsyncSession,
        owner_user_id: str,
        data: dict[str, Any],
        source_version: str,
    ) -> None:
        """Simpan proposed facts hasil flow (confidence dipetakan ke 0..1)."""
        from temanbule.modules.conversations.facts import FactsService

        facts = data.get("facts")
        if not isinstance(facts, list):
            return  # tidak ada fakta pada range ini; job tetap sukses
        confidence_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
        service = FactsService(session)
        for item in facts:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            value = item.get("value")
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            if not key.strip() or not value.strip():
                continue
            confidence = confidence_map.get(str(item.get("confidence", "low")), 0.3)
            await service.upsert_fact(
                user_id=owner_user_id,
                fact_key=key,
                value=value,
                confidence=confidence,
                provenance_ref=str(item.get("evidence", source_version))[:255],
                source_version=source_version[:255],
            )

    async def _persist_assessment(
        self,
        *,
        session: AsyncSession,
        owner_user_id: str,
        session_id: str,
        source_start: int,
        source_end: int,
        flow_version: str,
        data: dict[str, Any],
    ) -> None:
        """Simpan assessment hasil flow (dimensions bounded, idempoten)."""
        from temanbule.modules.conversations.assessments import AssessmentService

        dimensions_raw = data.get("dimensions")
        if not isinstance(dimensions_raw, dict) or not dimensions_raw:
            raise DependencyUnavailableError(
                "Assessment flow tidak mengembalikan dimensions.",
                code="ASSESSMENT_INVALID_OUTPUT",
            )
        dimensions: dict[str, int] = {}
        for name, score in dimensions_raw.items():
            try:
                value = int(score)
            except (TypeError, ValueError):
                continue
            dimensions[str(name)] = max(0, min(100, value))
        if not dimensions:
            raise DependencyUnavailableError(
                "Assessment flow dimensions tidak valid.",
                code="ASSESSMENT_INVALID_OUTPUT",
            )
        suggested = data.get("estimated_level")
        rubric = data.get("rubric_version") or "rubric-v1"
        service = AssessmentService(session)
        await service.record_assessment(
            user_id=owner_user_id,
            session_id=session_id,
            evidence_start=source_start,
            evidence_end=source_end,
            rubric_version=str(rubric),
            dimensions=dimensions,
            suggested_level=str(suggested) if isinstance(suggested, str) else None,
            flow_version=flow_version,
        )

    # --- Podcast document ingestion (Langflow background) -------------------

    async def _run_podcast_ingestion(
        self,
        *,
        session: AsyncSession,
        job: BackgroundJob,
        payload: dict[str, Any],
    ) -> None:
        """Dua langkah idempoten: dispatch ke Langflow lalu poll sampai terminal.

        - ``dispatch``: trigger flow mode background; vendor ``job_id`` disimpan
          di checkpoint job dan attempt (reconciliation, langflow-flows.md:86).
        - ``poll``: cek status terminal; masih berjalan → DependencyUnavailableError
          ``PODCAST_INGESTION_RUNNING`` agar runner menjadwalkan ulang dengan
          backoff (job tetap nonterminal, tanpa dispatch ganda).
        """
        from temanbule.modules.catalog.flows import resolve_flow
        from temanbule.modules.podcasts.services import PodcastService

        if self._podcast_adapter is None:  # guard untuk type narrowing
            raise DependencyUnavailableError(
                "Podcast adapter belum terkonfigurasi.", code="PODCAST_ADAPTER_MISSING"
            )
        adapter = self._podcast_adapter
        step = str(payload.get("step", "dispatch"))
        checkpoint = json.loads(job.checkpoint) if job.checkpoint else {}

        binding = await resolve_flow(
            session,
            environment=self._settings.app_env,
            purpose=JOB_PURPOSE_PODCAST_INGESTION,
            flow_version=payload["flow_version"],
        )

        if step == "dispatch":
            langflow_job_id = await adapter.trigger_document_ingestion(
                binding,
                {
                    "schema_version": binding.input_schema_version,
                    "request_id": new_ulid(),
                    "purpose": JOB_PURPOSE_PODCAST_INGESTION,
                    "user": {"user_id": payload["owner_user_id"]},
                    "podcast": {
                        "podcast_id": payload["podcast_id"],
                        "source_version_id": payload["source_version_id"],
                    },
                    "source": {
                        "media_id": payload["media_id"],
                        "storage_key": payload["storage_key"],
                        "checksum": payload["checksum"],
                    },
                },
            )
            payload["step"] = "poll"
            job.payload = json.dumps(payload, sort_keys=True)
            job.checkpoint = json.dumps(
                {"langflow_job_id": langflow_job_id, "poll_count": 0}, sort_keys=True
            )
            # Retry deterministik untuk menulis vendor_job_id pada attempt
            # dilakukan runner via checkpoint; job tetap sukses pada poll berikut.
            job.state = JOB_STATE_PENDING
            job.run_after = datetime.now(UTC) + timedelta(
                seconds=PODCAST_INGESTION_POLL_INTERVAL_SECONDS
            )
            return

        # step == "poll"
        langflow_job_id = checkpoint.get("langflow_job_id")
        if not isinstance(langflow_job_id, str) or not langflow_job_id:
            raise ValidationError("Checkpoint ingestion podcast tanpa langflow_job_id.")
        try:
            output = await adapter.poll_document_ingestion(
                job_id=langflow_job_id, timeout_ms=binding.timeout_ms
            )
        except DependencyUnavailableError as exc:
            if exc.code == "PODCAST_INGESTION_FAILED":
                # Terminal failure di vendor: tandai source/podcast gagal permanen.
                await PodcastService(session).mark_source_failed(
                    source_version_id=payload["source_version_id"]
                )
                raise ValidationError("Ingestion podcast gagal di Langflow.") from exc
            if exc.code == "PODCAST_INGESTION_RUNNING":
                poll_count = int(checkpoint.get("poll_count", 0)) + 1
                if poll_count > PODCAST_INGESTION_MAX_POLLS:
                    await PodcastService(session).mark_source_failed(
                        source_version_id=payload["source_version_id"]
                    )
                    raise ValidationError(
                        "Ingestion podcast melewati batas waktu poll."
                    ) from exc
                job.checkpoint = json.dumps(
                    {"langflow_job_id": langflow_job_id, "poll_count": poll_count},
                    sort_keys=True,
                )
            raise
        page_count = output["page_count"]
        await PodcastService(session).apply_ingestion_result(
            source_version_id=payload["source_version_id"],
            page_count=page_count,
            langflow_job_id=langflow_job_id,
        )

    # --- Finalize ---------------------------------------------------------

    async def _finalize(
        self,
        *,
        claimed: BackgroundJob,
        attempt: BackgroundJobAttempt,
        outcome: str,
        safe_error: str | None,
        ended: datetime,
    ) -> None:
        """Tulis attempt + transisi state job secara fenced (anti stale)."""
        async with self._session_factory() as session:
            job = await session.get(BackgroundJob, claimed.id)
            if job is None or job.fencing_token != claimed.fencing_token:
                # Worker lain mengambil alih lease; buang hasil (fencing).
                logger.warning(
                    "job_fencing_conflict",
                    extra={"job_id": claimed.id},
                )
                return
            attempt.state = JOB_STATE_SUCCEEDED if outcome == JOB_STATE_SUCCEEDED else outcome
            attempt.ended_at = ended
            attempt.safe_error = safe_error
            session.add(attempt)

            # attempt_count sudah dinaikkan saat claim; jangan dinaikkan lagi.
            job.locked_until = None
            job.safe_error = safe_error
            if outcome == JOB_STATE_SUCCEEDED:
                job.state = JOB_STATE_SUCCEEDED
            elif outcome == JOB_STATE_PENDING:
                if job.attempt_count >= self._max_attempts:
                    job.state = JOB_STATE_FAILED
                    logger.error(
                        "job_dead_letter",
                        extra={"job_id": job.id, "purpose": job.purpose},
                    )
                elif job.state == JOB_STATE_RUNNING:
                    # Processor belum menjadwalkan dirinya; backoff default.
                    job.state = JOB_STATE_PENDING
                    delay = _RETRY_BASE_DELAY_SECONDS * job.attempt_count
                    job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
                # else: processor sudah mengatur state/run_after sendiri
                # (mis. dispatch/poll ingestion podcast dengan interval tetap).
            else:  # permanent failure
                job.state = JOB_STATE_FAILED
            await session.commit()


class _SpecEmbeddingBridge:
    """Jembatan EmbeddingPort: run_embedding_job memanggil embed(profile=...).

    Menyediakan spec yang sudah di-resolve agar adapter konkret menerima
    EmbeddingProfileSpec, bukan baris ORM EmbeddingProfile.
    """

    def __init__(self, adapter: DualEmbeddingAdapter, spec: Any) -> None:
        self._adapter = adapter
        self._spec = spec

    async def embed(
        self,
        *,
        chunk_text: str,
        profile: Any,
        chunk_id: str,
        source_version: str,
        content_hash: str,
    ) -> str:
        return await self._adapter.embed(
            chunk_text=chunk_text,
            profile=self._spec,
            chunk_id=chunk_id,
            source_version=source_version,
            content_hash=content_hash,
        )


async def run_job_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    extractor: LangflowExtractionAdapter | None,
    embedder: DualEmbeddingAdapter | None,
    podcast_adapter: Any | None = None,
    shutdown: asyncio.Event,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Loop utama job runner: proses antrian sampai shutdown."""
    runner = JobRunner(
        settings=settings,
        session_factory=session_factory,
        extractor=extractor,
        embedder=embedder,
        podcast_adapter=podcast_adapter,
        lease_seconds=settings.realtime_lease_ttl_seconds or 60,
        max_attempts=settings.embedding_max_attempts or 5,
    )
    logger.info("job_runner_started")
    while not shutdown.is_set():
        try:
            processed = await runner.run_once(batch=5)
        except Exception as exc:
            logger.exception(
                "job_runner_iteration_failed",
                exc_info=exc,
                extra={"error_code": type(exc).__name__},
            )
            processed = 0
        if processed == 0:
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=poll_interval_seconds)
            except TimeoutError:
                pass
    logger.info("job_runner_stopped")
