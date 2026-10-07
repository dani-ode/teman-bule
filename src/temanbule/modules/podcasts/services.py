"""Podcast service (Phase 7): sources, scripts, segments, playbacks, cache.

Kontrak (implementation-plan.md Phase 7, realtime-podcast.md, langflow-flows.md):
- Paper grounding: segments wajib citations ke canonical chunks; script
  memvalidasi speaker hanya Elean/Willy sebelum ready.
- Generation idempoten: satu podcast satu active generation job; regenerate
  = revision baru, tidak mendebet ulang generation yang sama (dedupe job).
- Dua suara berbeda: script ready wajib punya ≥2 agent_version berbeda.
- Playback: single-director, lease/fencing, cursor/offset checkpoint,
  interruption branches dengan deadline; repeated interruption tidak
  memperpanjang selamanya (bounded branch count).
- Audio cache per (segment, voice_config_hash); playback cache tanpa
  invokasi baru tidak ditagih ulang.
- Script generation/TTS invocation nyata menunggu DEC-13/14; service ini
  pemilik state otoritatif + validasi.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.flows import resolve_flow
from temanbule.modules.catalog.models import Agent, AgentVersion
from temanbule.modules.catalog.services import CatalogService, RuntimeSnapshotBuilder
from temanbule.modules.media.models import MediaObject
from temanbule.modules.podcasts.langflow_adapter import (
    LangflowPodcastAdapter,
    PodcastScriptResult,
)
from temanbule.modules.podcasts.models import (
    Podcast,
    PodcastAudioCache,
    PodcastPlayback,
    PodcastScriptVersion,
    PodcastSegment,
    PodcastSourceVersion,
)
from temanbule.modules.reliability.outbox import record_outbox_event
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid, sha256_hex
from temanbule.platform.settings import Settings

SCRIPT_READY = "ready"
SCRIPT_DRAFT = "draft"

PLAYBACK_ENDED = "ended"
PLAYBACK_PLAYING = "playing"
PLAYBACK_INTERRUPTED = "interrupted"

MAX_INTERRUPTION_BRANCHES = 10

# Purpose flow podcast (registry ai_flow_registry per environment).
PURPOSE_DOCUMENT_INGESTION = "podcast_document_ingestion"
PURPOSE_SCRIPT_GENERATION = "podcast_script_generation"


class PodcastService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings
        self.snapshots = RuntimeSnapshotBuilder(session)
        self.catalog = CatalogService(session)

    async def create_podcast(self, *, user_id: str, title: str) -> Podcast:
        if not title.strip():
            raise ValidationError("Title kosong.")
        podcast = Podcast(id=new_ulid(), user_id=user_id, title=title.strip())
        self.session.add(podcast)
        await self.session.flush()
        return podcast

    async def add_source(
        self, *, user_id: str, podcast_id: str, media_id: str
    ) -> PodcastSourceVersion:
        """Tambah source version dari media finalized+clean milik user."""
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        media = (
            await self.session.execute(
                select(MediaObject).where(MediaObject.id == media_id)
            )
        ).scalar_one_or_none()
        if media is None or media.owner_user_id != user_id:
            raise NotFoundError("Media tidak ditemukan.")
        if media.status != "finalized" or media.scan_state != "clean":
            raise ConflictError(
                "Media belum finalized/clean.",
                code="MEDIA_NOT_READY",
            )
        if media.media_type != "pdf":
            raise ValidationError(
                "Podcast source wajib PDF.",
                details=[{"field": "media_type", "message": media.media_type}],
            )
        revision = (
            await self.session.execute(
                select(func.coalesce(func.max(PodcastSourceVersion.revision), 0)).where(
                    PodcastSourceVersion.podcast_id == podcast_id
                )
            )
        ).scalar_one() + 1
        source = PodcastSourceVersion(
            id=new_ulid(),
            podcast_id=podcast_id,
            revision=revision,
            media_id=media.id,
            checksum=media.checksum,
            parse_status="pending",
        )
        self.session.add(source)
        podcast.current_source_version_id = source.id
        podcast.state = "source_processing"
        # Outbox event atomik: worker mendispatch ingestion Langflow (background).
        record_outbox_event(
            self.session,
            aggregate_type="podcast_source",
            aggregate_id=source.id,
            aggregate_version=source.revision,
            event_type="podcast.source_uploaded.v1",
            payload={
                "podcast_id": podcast.id,
                "source_version_id": source.id,
                "owner_user_id": user_id,
                "media_id": media.id,
                "storage_key": media.storage_key,
                "checksum": media.checksum,
            },
        )
        await self.session.flush()
        return source

    async def mark_source_parsed(
        self, *, source_version_id: str, page_count: int
    ) -> PodcastSourceVersion:
        source = (
            await self.session.execute(
                select(PodcastSourceVersion)
                .where(PodcastSourceVersion.id == source_version_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if source is None:
            raise NotFoundError("Source version tidak ditemukan.")
        if source.parse_status == "parsed":
            return source
        if page_count <= 0:
            raise ValidationError("page_count harus positif.")
        source.parse_status = "parsed"
        source.page_count = page_count
        podcast = (
            await self.session.execute(
                select(Podcast).where(Podcast.id == source.podcast_id)
            )
        ).scalar_one()
        podcast.state = "source_ready"
        await self.session.flush()
        return source

    async def apply_ingestion_result(
        self,
        *,
        source_version_id: str,
        page_count: int,
        langflow_job_id: str | None = None,
    ) -> PodcastSourceVersion:
        """Finalisasi ingestion: tandai parsed, simpan vendor job_id, event selesai.

        Dipanggil worker setelah job Langflow terminal completed. Idempoten:
        source yang sudah parsed dikembalikan apa adanya.
        """
        source = await self.mark_source_parsed(
            source_version_id=source_version_id, page_count=page_count
        )
        if langflow_job_id is not None:
            podcast = (
                await self.session.execute(
                    select(Podcast).where(Podcast.id == source.podcast_id).with_for_update()
                )
            ).scalar_one()
            podcast.generation_job_id = langflow_job_id
        record_outbox_event(
            self.session,
            aggregate_type="podcast_source",
            aggregate_id=source.id,
            aggregate_version=source.revision,
            event_type="podcast.source_processed.v1",
            payload={
                "podcast_id": source.podcast_id,
                "source_version_id": source.id,
                "page_count": page_count,
            },
        )
        await self.session.flush()
        return source

    async def mark_source_failed(self, *, source_version_id: str) -> None:
        """Tandai ingestion gagal permanen (job Langflow terminal failed)."""
        source = (
            await self.session.execute(
                select(PodcastSourceVersion)
                .where(PodcastSourceVersion.id == source_version_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if source is None:
            raise NotFoundError("Source version tidak ditemukan.")
        if source.parse_status == "parsed":
            return
        source.parse_status = "failed"
        podcast = (
            await self.session.execute(
                select(Podcast).where(Podcast.id == source.podcast_id).with_for_update()
            )
        ).scalar_one()
        podcast.state = "failed"
        await self.session.flush()

    async def create_script_version(
        self,
        *,
        user_id: str,
        podcast_id: str,
        source_version_id: str,
        outline: str,
        target_duration_seconds: int,
        agent_code: str = "elean",
    ) -> PodcastScriptVersion:
        """Buat script version draft (hasil generation disimpan via job)."""
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        source = (
            await self.session.execute(
                select(PodcastSourceVersion).where(
                    PodcastSourceVersion.id == source_version_id,
                    PodcastSourceVersion.podcast_id == podcast_id,
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise NotFoundError("Source version tidak ditemukan.")
        if source.parse_status != "parsed":
            raise ConflictError(
                "Source belum parsed; script tidak grounded.",
                code="SOURCE_NOT_PARSED",
            )
        if target_duration_seconds <= 0:
            raise ValidationError("target_duration_seconds harus positif.")
        snapshot = await self.snapshots.build_for_user(user_id=user_id, agent_code=agent_code)
        revision = (
            await self.session.execute(
                select(func.coalesce(func.max(PodcastScriptVersion.revision), 0)).where(
                    PodcastScriptVersion.podcast_id == podcast_id
                )
            )
        ).scalar_one() + 1
        script = PodcastScriptVersion(
            id=new_ulid(),
            podcast_id=podcast_id,
            source_version_id=source_version_id,
            revision=revision,
            runtime_snapshot_id=snapshot.id,
            outline=outline,
            target_duration_seconds=target_duration_seconds,
            status=SCRIPT_DRAFT,
        )
        self.session.add(script)
        podcast.state = "script_generating"
        podcast.generation_job_id = new_ulid()
        await self.session.flush()
        return script

    async def add_segment(
        self,
        *,
        user_id: str,
        podcast_id: str,
        script_version_id: str,
        agent_version_id: str,
        text: str,
        citations: list[str],
    ) -> PodcastSegment:
        """Tambah segment dengan citation wajib (paper grounding)."""
        await self._owned_podcast(user_id, podcast_id)
        script = (
            await self.session.execute(
                select(PodcastScriptVersion).where(
                    PodcastScriptVersion.id == script_version_id,
                    PodcastScriptVersion.podcast_id == podcast_id,
                )
            )
        ).scalar_one_or_none()
        if script is None:
            raise NotFoundError("Script version tidak ditemukan.")
        if script.status == SCRIPT_READY:
            raise ConflictError(
                "Script sudah ready (immutable).",
                code="SCRIPT_IMMUTABLE",
            )
        if not text.strip():
            raise ValidationError("Segment text kosong.")
        if not citations:
            raise ValidationError(
                "Segment wajib citations (paper grounding).",
                details=[{"field": "citations", "message": "minimal satu"}],
            )
        agent_version = (
            await self.session.execute(
                select(AgentVersion).where(AgentVersion.id == agent_version_id)
            )
        ).scalar_one_or_none()
        if agent_version is None:
            raise NotFoundError("Agent version tidak ditemukan.")
        position = (
            await self.session.execute(
                select(func.coalesce(func.max(PodcastSegment.position), 0)).where(
                    PodcastSegment.script_version_id == script_version_id
                )
            )
        ).scalar_one() + 1
        segment = PodcastSegment(
            id=new_ulid(),
            script_version_id=script_version_id,
            position=position,
            agent_version_id=agent_version_id,
            text=text.strip(),
            citations=json.dumps(citations),
        )
        self.session.add(segment)
        await self.session.flush()
        return segment

    async def mark_script_ready(
        self, *, user_id: str, podcast_id: str, script_version_id: str
    ) -> PodcastScriptVersion:
        """Validasi sebelum ready: dua agent berbeda (dua suara), segments ada."""
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        script = (
            await self.session.execute(
                select(PodcastScriptVersion).where(
                    PodcastScriptVersion.id == script_version_id,
                    PodcastScriptVersion.podcast_id == podcast_id,
                )
            )
        ).scalar_one_or_none()
        if script is None:
            raise NotFoundError("Script version tidak ditemukan.")
        if script.status == SCRIPT_READY:
            return script

        agent_ids = (
            (
                await self.session.execute(
                    select(PodcastSegment.agent_version_id)
                    .where(PodcastSegment.script_version_id == script_version_id)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if len(agent_ids) < 2:
            raise ConflictError(
                "Script wajib dua agent berbeda (dua suara Elean/Willy).",
                code="SCRIPT_SINGLE_VOICE",
            )
        # Validasi kedua agent adalah elean & willy
        agent_codes = (
            (
                await self.session.execute(
                    select(Agent.code)
                    .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                    .where(AgentVersion.id.in_(agent_ids))
                )
            )
            .scalars()
            .all()
        )
        if not {"elean", "willy"}.issubset(set(agent_codes)):
            raise ConflictError(
                "Segment speaker wajib Elean dan Willy.",
                code="SCRIPT_INVALID_SPEAKERS",
            )
        script.status = SCRIPT_READY
        podcast.state = "script_ready"
        podcast.current_script_version_id = script.id
        podcast.generation_job_id = None
        await self.session.flush()
        return script

    async def generate_script_and_start_playback(
        self,
        *,
        user_id: str,
        podcast_id: str,
        target_duration_seconds: int,
        lease_owner: str,
        deadline_seconds: int,
        adapter: LangflowPodcastAdapter,
    ) -> tuple[PodcastScriptVersion, PodcastPlayback]:
        """Play = generate script baru (sync) lalu playback baru; 1 script = 1 session.

        Script TIDAK dipakai ulang lintas klik play: setiap klik men-generate
        script version baru lewat flow ``podcast_script_generation`` (mode sync)
        lalu membuat playback + conversation session baru yang menguncinya.
        Kegagalan vendor → DependencyUnavailableError; tidak ada script palsu.
        """
        if self.settings is None:
            raise ConflictError(
                "PodcastService memerlukan settings untuk script generation.",
                code="SETTINGS_REQUIRED",
            )
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        source_id = podcast.current_source_version_id
        if source_id is None:
            raise ConflictError(
                "Podcast belum punya source dokumen.",
                code="SOURCE_MISSING",
            )
        source = (
            await self.session.execute(
                select(PodcastSourceVersion).where(
                    PodcastSourceVersion.id == source_id,
                    PodcastSourceVersion.podcast_id == podcast_id,
                )
            )
        ).scalar_one()
        if source.parse_status != "parsed":
            raise ConflictError(
                "Dokumen masih diproses; cek status ingestion.",
                code="SOURCE_NOT_PARSED",
            )
        if target_duration_seconds <= 0:
            raise ValidationError("target_duration_seconds harus positif.")

        snapshot = await self.snapshots.build_for_user(user_id=user_id, agent_code="elean")
        binding = await resolve_flow(
            self.session,
            environment=self.settings.app_env,
            purpose=PURPOSE_SCRIPT_GENERATION,
        )
        request_id = new_ulid()
        envelope = {
            "schema_version": binding.input_schema_version,
            "request_id": request_id,
            "purpose": PURPOSE_SCRIPT_GENERATION,
            "user": {"user_id": user_id},
            "podcast": {
                "podcast_id": podcast.id,
                "source_version_id": source.id,
                "media_id": source.media_id,
                "page_count": source.page_count,
            },
            "session": {"runtime_snapshot_id": snapshot.id},
            "input": {"target_duration_seconds": target_duration_seconds},
        }
        result = await adapter.run_script_generation(binding, envelope)
        script = await self._persist_generated_script(
            podcast=podcast,
            source=source,
            snapshot_id=snapshot.id,
            result=result,
            target_duration_seconds=target_duration_seconds,
        )
        playback = await self.start_playback(
            user_id=user_id,
            podcast_id=podcast_id,
            script_version_id=script.id,
            lease_owner=lease_owner,
            deadline_seconds=deadline_seconds,
        )
        return script, playback

    async def _persist_generated_script(
        self,
        *,
        podcast: Podcast,
        source: PodcastSourceVersion,
        snapshot_id: str,
        result: PodcastScriptResult,
        target_duration_seconds: int,
    ) -> PodcastScriptVersion:
        """Simpan script generation sebagai version ready immutable + segments.

        Speaker dipetakan ke active agent version (Elean/Willy); ready wajib
        dua suara berbeda dan setiap segment bercitation (paper grounding).
        """
        elean_version = await self.catalog.get_active_agent_version("elean")
        willy_version = await self.catalog.get_active_agent_version("willy")
        version_by_speaker = {"elean": elean_version.id, "willy": willy_version.id}
        revision = (
            await self.session.execute(
                select(func.coalesce(func.max(PodcastScriptVersion.revision), 0)).where(
                    PodcastScriptVersion.podcast_id == podcast.id
                )
            )
        ).scalar_one() + 1
        script = PodcastScriptVersion(
            id=new_ulid(),
            podcast_id=podcast.id,
            source_version_id=source.id,
            revision=revision,
            runtime_snapshot_id=snapshot_id,
            outline=result.outline,
            target_duration_seconds=target_duration_seconds,
            estimated_duration_seconds=result.estimated_duration_seconds,
            status=SCRIPT_READY,
        )
        self.session.add(script)
        await self.session.flush()
        for position, segment_output in enumerate(result.segments, start=1):
            self.session.add(
                PodcastSegment(
                    id=new_ulid(),
                    script_version_id=script.id,
                    position=position,
                    agent_version_id=version_by_speaker[segment_output.speaker],
                    text=segment_output.text.strip(),
                    citations=json.dumps(segment_output.citations),
                    estimated_ms=segment_output.estimated_ms,
                )
            )
        podcast.state = "script_ready"
        podcast.current_script_version_id = script.id
        podcast.generation_job_id = None
        record_outbox_event(
            self.session,
            aggregate_type="podcast",
            aggregate_id=podcast.id,
            aggregate_version=revision,
            event_type="podcast.script_ready.v1",
            payload={
                "podcast_id": podcast.id,
                "script_version_id": script.id,
                "revision": revision,
                "segment_count": len(result.segments),
            },
        )
        await self.session.flush()
        return script

    async def start_playback(
        self,
        *,
        user_id: str,
        podcast_id: str,
        script_version_id: str,
        lease_owner: str,
        deadline_seconds: int,
    ) -> PodcastPlayback:
        """Mulai playback single-director dengan lease/fencing + deadline."""
        await self._owned_podcast(user_id, podcast_id, for_update=True)
        script = (
            await self.session.execute(
                select(PodcastScriptVersion).where(
                    PodcastScriptVersion.id == script_version_id,
                    PodcastScriptVersion.podcast_id == podcast_id,
                    PodcastScriptVersion.status == SCRIPT_READY,
                )
            )
        ).scalar_one_or_none()
        if script is None:
            raise ConflictError(
                "Script belum ready.",
                code="SCRIPT_NOT_READY",
            )
        from temanbule.modules.conversations.models import ConversationSession

        conversation = ConversationSession(
            id=new_ulid(),
            user_id=user_id,
            kind="podcast",
            state="active",
            runtime_snapshot_id=script.runtime_snapshot_id,
        )
        self.session.add(conversation)
        await self.session.flush()
        playback = PodcastPlayback(
            id=new_ulid(),
            podcast_id=podcast_id,
            script_version_id=script_version_id,
            session_id=conversation.id,
            room_name=f"podcast-{conversation.id}",
            state=PLAYBACK_PLAYING,
            lease_owner=lease_owner,
            fencing_token=1,
            lease_until=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
            deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
        )
        self.session.add(playback)
        await self.session.flush()
        return playback

    async def interrupt_playback(
        self,
        *,
        user_id: str,
        playback_id: str,
        fencing_token: int,
        lease_owner: str,
        branch_ref: str,
    ) -> PodcastPlayback:
        """Interruption branch; bounded — tidak memperpanjang selamanya."""
        playback = await self._owned_playback(user_id, playback_id, for_update=True)
        self._assert_fencing(playback, fencing_token, lease_owner)
        if playback.state != PLAYBACK_PLAYING:
            raise ConflictError(
                "Playback tidak sedang playing.",
                code="PLAYBACK_STATE_INVALID",
            )
        branch_count = (
            await self.session.execute(
                select(func.count(PodcastPlayback.id)).where(
                    PodcastPlayback.session_id == playback.session_id,
                    PodcastPlayback.branch_ref.isnot(None),
                )
            )
        ).scalar_one()
        if branch_count >= MAX_INTERRUPTION_BRANCHES:
            raise ConflictError(
                "Batas interruption branch tercapai.",
                code="PLAYBACK_INTERRUPTION_LIMIT",
            )
        playback.state = PLAYBACK_INTERRUPTED
        playback.branch_ref = branch_ref
        playback.epoch += 1
        await self.session.flush()
        return playback

    async def end_playback(
        self, *, user_id: str, playback_id: str, end_reason: str
    ) -> PodcastPlayback:
        playback = await self._owned_playback(user_id, playback_id, for_update=True)
        if playback.state == PLAYBACK_ENDED:
            return playback
        playback.state = PLAYBACK_ENDED
        playback.end_reason = end_reason
        playback.lease_owner = None
        playback.lease_until = None
        await self.session.flush()
        return playback

    async def cache_audio(
        self,
        *,
        user_id: str,
        segment_id: str,
        voice_config_hash: str,
        media_id: str,
        checksum: str,
    ) -> tuple[PodcastAudioCache, bool]:
        """Cache audio per (segment, voice hash); replay tanpa regenerate."""
        existing = (
            await self.session.execute(
                select(PodcastAudioCache).where(
                    PodcastAudioCache.segment_id == segment_id,
                    PodcastAudioCache.voice_config_hash == voice_config_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        cache = PodcastAudioCache(
            id=new_ulid(),
            owner_user_id=user_id,
            segment_id=segment_id,
            voice_config_hash=voice_config_hash,
            media_id=media_id,
            checksum=checksum or sha256_hex(media_id),
        )
        self.session.add(cache)
        await self.session.flush()
        return cache, True

    async def _owned_podcast(
        self, user_id: str, podcast_id: str, *, for_update: bool = False
    ) -> Podcast:
        stmt = select(Podcast).where(Podcast.id == podcast_id)
        if for_update:
            stmt = stmt.with_for_update()
        podcast = (await self.session.execute(stmt)).scalar_one_or_none()
        if podcast is None or podcast.user_id != user_id:
            raise NotFoundError("Podcast tidak ditemukan.")
        return podcast

    async def get_podcast(self, *, user_id: str, podcast_id: str) -> Podcast:
        return await self._owned_podcast(user_id, podcast_id)

    async def update_title(
        self, *, user_id: str, podcast_id: str, title: str
    ) -> Podcast:
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        if not title.strip():
            raise ValidationError("Title kosong.")
        podcast.title = title.strip()
        await self.session.flush()
        return podcast

    async def delete_podcast(self, *, user_id: str, podcast_id: str) -> None:
        podcast = await self._owned_podcast(user_id, podcast_id, for_update=True)
        await self.session.delete(podcast)
        await self.session.flush()

    async def list_segments(
        self, *, user_id: str, podcast_id: str
    ) -> list[PodcastSegment]:
        await self._owned_podcast(user_id, podcast_id)
        rows = (
            (
                await self.session.execute(
                    select(PodcastSegment)
                    .join(
                        PodcastScriptVersion,
                        PodcastSegment.script_version_id == PodcastScriptVersion.id,
                    )
                    .where(PodcastScriptVersion.podcast_id == podcast_id)
                    .order_by(PodcastSegment.position.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def get_playback(
        self, *, user_id: str, podcast_id: str, playback_id: str
    ) -> PodcastPlayback:
        await self._owned_podcast(user_id, podcast_id)
        playback = (
            await self.session.execute(
                select(PodcastPlayback).where(PodcastPlayback.id == playback_id)
            )
        ).scalar_one_or_none()
        if playback is None or playback.podcast_id != podcast_id:
            raise NotFoundError("Playback tidak ditemukan.")
        return playback

    async def _owned_playback(
        self, user_id: str, playback_id: str, *, for_update: bool = False
    ) -> PodcastPlayback:
        stmt = select(PodcastPlayback).where(PodcastPlayback.id == playback_id)
        if for_update:
            stmt = stmt.with_for_update()
        playback = (await self.session.execute(stmt)).scalar_one_or_none()
        if playback is None:
            raise NotFoundError("Playback tidak ditemukan.")
        podcast = (
            await self.session.execute(
                select(Podcast).where(Podcast.id == playback.podcast_id)
            )
        ).scalar_one()
        if podcast.user_id != user_id:
            raise NotFoundError("Playback tidak ditemukan.")
        return playback

    def _assert_fencing(
        self, playback: PodcastPlayback, fencing_token: int, lease_owner: str
    ) -> None:
        if playback.lease_owner is None:
            raise ConflictError("Playback tanpa lease aktif.", code="PLAYBACK_NO_LEASE")
        if playback.lease_owner != lease_owner or playback.fencing_token != fencing_token:
            raise ConflictError(
                "Fencing token/lease owner tidak cocok.",
                code="PLAYBACK_FENCING_MISMATCH",
            )
