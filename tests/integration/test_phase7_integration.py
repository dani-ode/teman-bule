"""Integration tests Phase 7: podcast lifecycle.

Menutup exit criteria Phase 7:
- Paper grounding: segment wajib citations; source belum parsed → script ditolak
- Dua suara: script ready wajib Elean+Willy; satu suara → ditolak
- No duplicate generation debit: audio cache replay tanpa regenerate
- Interruption branches bounded; fencing; graceful end idempoten
- Cross-owner denied
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    AiFlowRegistry,
    Plan,
    PlanPolicyVersion,
)
from temanbule.modules.identity.models import User
from temanbule.modules.media.services import MediaService
from temanbule.modules.podcasts.langflow_adapter import (
    PodcastScriptResult,
    PodcastSegmentOutput,
)
from temanbule.modules.podcasts.models import PodcastSegment
from temanbule.modules.podcasts.services import PodcastService
from temanbule.modules.reliability.models import BackgroundJob, OutboxEvent
from temanbule.platform.errors import (
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)
from temanbule.platform.security import new_ulid
from temanbule.platform.settings import Settings
from temanbule.worker.handlers import (
    PODCAST_INGESTION_FLOW_VERSION,
    handle_podcast_source_uploaded,
)

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")


@pytest.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL tidak diset")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _fixture(db: AsyncSession) -> dict[str, str]:
    user = User(id=new_ulid(), normalized_email=f"pod-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()

    agent_versions: dict[str, str] = {}
    for code, name in (("elean", "Elean"), ("willy", "Willy")):
        agent = (await db.execute(select(Agent).where(Agent.code == code))).scalar_one_or_none()
        if agent is None:
            agent = Agent(id=new_ulid(), code=code, display_name=name, status="active")
            db.add(agent)
            await db.flush()
        if agent.active_version_id is None:
            version = AgentVersion(id=new_ulid(), agent_id=agent.id, revision=1, status="published")
            db.add(version)
            await db.flush()
            agent.active_version_id = version.id
            await db.flush()
        agent_versions[code] = agent.active_version_id

    plan = (await db.execute(select(Plan).where(Plan.code == "vip"))).scalar_one_or_none()
    if plan is None:
        plan = Plan(code="vip", status="active")
        policy = PlanPolicyVersion(
            id=new_ulid(), plan_code="vip", revision=1, policy="{}", status="published"
        )
        db.add_all([plan, policy])
        await db.flush()
        plan.policy_version = 1
        await db.flush()

    from temanbule.modules.catalog.plan_selection import PlanSelectionService

    await PlanSelectionService(db).select_plan(user_id=user.id, plan_code="vip")

    media_service = MediaService(db)
    media = await media_service.register_upload(user_id=user.id, media_type="pdf", size_bytes=1000)
    await media_service.finalize_upload(
        user_id=user.id, media_id=media.id, checksum="pdf-sum", actual_bytes=1000
    )
    await media_service.apply_scan_result(media_id=media.id, scan_state="clean")

    return {
        "user_id": user.id,
        "media_id": media.id,
        "elean_version": agent_versions["elean"],
        "willy_version": agent_versions["willy"],
    }


async def _ready_source(db: AsyncSession, fx: dict[str, str]) -> tuple[str, str]:
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="My Paper")
    source = await service.add_source(
        user_id=fx["user_id"], podcast_id=podcast.id, media_id=fx["media_id"]
    )
    await service.mark_source_parsed(source_version_id=source.id, page_count=12)
    return podcast.id, source.id


@requires_db
async def test_source_requires_clean_pdf(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Doc")

    # Media audio → ditolak
    media_service = MediaService(db)
    audio = await media_service.register_upload(
        user_id=fx["user_id"], media_type="audio", size_bytes=100
    )
    await media_service.finalize_upload(
        user_id=fx["user_id"], media_id=audio.id, checksum="a", actual_bytes=100
    )
    await media_service.apply_scan_result(media_id=audio.id, scan_state="clean")
    with pytest.raises(ValidationError, match="PDF"):
        await service.add_source(user_id=fx["user_id"], podcast_id=podcast.id, media_id=audio.id)


@requires_db
async def test_script_requires_parsed_source_grounding(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Paper")
    source = await service.add_source(
        user_id=fx["user_id"], podcast_id=podcast.id, media_id=fx["media_id"]
    )
    # Belum parsed → script ditolak
    with pytest.raises(ConflictError, match="grounded"):
        await service.create_script_version(
            user_id=fx["user_id"], podcast_id=podcast.id,
            source_version_id=source.id, outline="[]", target_duration_seconds=300,
        )


@requires_db
async def test_segments_require_citations(db: AsyncSession) -> None:
    fx = await _fixture(db)
    podcast_id, source_id = await _ready_source(db, fx)
    service = PodcastService(db)
    script = await service.create_script_version(
        user_id=fx["user_id"], podcast_id=podcast_id,
        source_version_id=source_id, outline="[]", target_duration_seconds=300,
    )
    with pytest.raises(ValidationError, match="citations"):
        await service.add_segment(
            user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
            agent_version_id=fx["elean_version"], text="Hello", citations=[],
        )


@requires_db
async def test_script_ready_requires_two_voices(db: AsyncSession) -> None:
    fx = await _fixture(db)
    podcast_id, source_id = await _ready_source(db, fx)
    service = PodcastService(db)
    script = await service.create_script_version(
        user_id=fx["user_id"], podcast_id=podcast_id,
        source_version_id=source_id, outline="[]", target_duration_seconds=300,
    )
    # Hanya satu suara → ready ditolak
    await service.add_segment(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        agent_version_id=fx["elean_version"], text="Intro", citations=["chunk-1"],
    )
    with pytest.raises(ConflictError, match="dua agent"):
        await service.mark_script_ready(
            user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id
        )

    # Tambah suara kedua → ready berhasil
    await service.add_segment(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        agent_version_id=fx["willy_version"], text="Response", citations=["chunk-2"],
    )
    ready = await service.mark_script_ready(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id
    )
    assert ready.status == "ready"

    # Segment baru pada script ready → ditolak (immutable)
    with pytest.raises(ConflictError, match="immutable"):
        await service.add_segment(
            user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
            agent_version_id=fx["elean_version"], text="Late", citations=["c"],
        )


@requires_db
async def test_audio_cache_no_duplicate_regeneration(db: AsyncSession) -> None:
    fx = await _fixture(db)
    podcast_id, source_id = await _ready_source(db, fx)
    service = PodcastService(db)
    script = await service.create_script_version(
        user_id=fx["user_id"], podcast_id=podcast_id,
        source_version_id=source_id, outline="[]", target_duration_seconds=300,
    )
    segment = await service.add_segment(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        agent_version_id=fx["elean_version"], text="Line", citations=["c1"],
    )
    cache, created = await service.cache_audio(
        user_id=fx["user_id"], segment_id=segment.id,
        voice_config_hash="voice-hash-1", media_id=fx["media_id"], checksum="s",
    )
    assert created is True
    # Replay: tidak regenerate (no duplicate generation debit)
    again, created_again = await service.cache_audio(
        user_id=fx["user_id"], segment_id=segment.id,
        voice_config_hash="voice-hash-1", media_id=fx["media_id"], checksum="s",
    )
    assert created_again is False
    assert again.id == cache.id


@requires_db
async def test_playback_interruption_bounded_and_fencing(db: AsyncSession) -> None:
    fx = await _fixture(db)
    podcast_id, source_id = await _ready_source(db, fx)
    service = PodcastService(db)
    script = await service.create_script_version(
        user_id=fx["user_id"], podcast_id=podcast_id,
        source_version_id=source_id, outline="[]", target_duration_seconds=300,
    )
    await service.add_segment(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        agent_version_id=fx["elean_version"], text="A", citations=["c1"],
    )
    await service.add_segment(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        agent_version_id=fx["willy_version"], text="B", citations=["c2"],
    )
    await service.mark_script_ready(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id
    )

    playback = await service.start_playback(
        user_id=fx["user_id"], podcast_id=podcast_id, script_version_id=script.id,
        lease_owner="director-1", deadline_seconds=3600,
    )
    assert playback.state == "playing"
    assert playback.deadline_at is not None

    # Fencing salah → ditolak
    with pytest.raises(ConflictError, match="Fencing"):
        await service.interrupt_playback(
            user_id=fx["user_id"], playback_id=playback.id,
            fencing_token=99, lease_owner="director-1", branch_ref="q1",
        )

    interrupted = await service.interrupt_playback(
        user_id=fx["user_id"], playback_id=playback.id,
        fencing_token=1, lease_owner="director-1", branch_ref="question-1",
    )
    assert interrupted.state == "interrupted"
    assert interrupted.epoch == 1

    ended = await service.end_playback(
        user_id=fx["user_id"], playback_id=playback.id, end_reason="user_end"
    )
    assert ended.state == "ended"
    again = await service.end_playback(
        user_id=fx["user_id"], playback_id=playback.id, end_reason="other"
    )
    assert again.end_reason == "user_end"


@requires_db
async def test_cross_owner_podcast_denied(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Private")
    stranger = new_ulid()
    with pytest.raises(NotFoundError):
        await service.add_source(user_id=stranger, podcast_id=podcast.id, media_id=fx["media_id"])


# --- Phase 11: ingestion Langflow background + play sync ---------------------


class _FakePodcastAdapter:
    """Double adapter: script generation sukses tanpa vendor Langflow."""

    async def run_script_generation(self, binding: object, envelope: dict) -> PodcastScriptResult:
        return PodcastScriptResult(
            outline="Diskusi paper",
            estimated_duration_seconds=300,
            segments=[
                PodcastSegmentOutput(speaker="elean", text="Halo", citations=["c1"]),
                PodcastSegmentOutput(speaker="willy", text="Hai", citations=["c2"]),
            ],
        )


class _FailingPodcastAdapter:
    async def run_script_generation(self, binding: object, envelope: dict) -> PodcastScriptResult:
        raise DependencyUnavailableError(
            "Script generation podcast belum dapat dikonfirmasi.",
            code="PODCAST_SCRIPT_OUTCOME_UNKNOWN",
        )


def _test_settings() -> Settings:
    return Settings(_env_file=None, app_env="test", langflow_api_key="synthetic")


async def _register_script_flow(db: AsyncSession) -> None:
    db.add(
        AiFlowRegistry(
            id=new_ulid(),
            environment="test",
            purpose="podcast_script_generation",
            flow_version="podcast-script.v1",
            langflow_flow_id="script-flow-under-test",
            input_schema_version="1",
            output_schema_version="1",
            tool_allowlist="[]",
            timeout_ms=60000,
            status="active",
        )
    )
    await db.flush()


@requires_db
async def test_add_source_dispatches_ingestion_job_via_outbox(db: AsyncSession) -> None:
    """Upload PDF → outbox atomik → handler buat SATU SQL job (idempoten)."""
    fx = await _fixture(db)
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Paper")
    source = await service.add_source(
        user_id=fx["user_id"], podcast_id=podcast.id, media_id=fx["media_id"]
    )
    event = (
        await db.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "podcast.source_uploaded.v1")
        )
    ).scalar_one()
    import json as _json

    payload = _json.loads(event.payload)
    assert payload["source_version_id"] == source.id

    # Dispatch dua kali (at-least-once delivery) → tetap satu job (dedupe).
    await handle_podcast_source_uploaded(db, payload)
    await handle_podcast_source_uploaded(db, payload)
    jobs = (
        (
            await db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.dedupe_key
                    == f"podcast-ingest:{source.id}:{PODCAST_INGESTION_FLOW_VERSION}"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    job_payload = _json.loads(jobs[0].payload)
    assert job_payload["step"] == "dispatch"
    assert job_payload["media_id"] == fx["media_id"]


@requires_db
async def test_play_generates_new_script_per_click(db: AsyncSession) -> None:
    """Play = script baru + playback/session baru; re-play = script version baru."""
    fx = await _fixture(db)
    podcast_id, _source_id = await _ready_source(db, fx)
    await _register_script_flow(db)
    service = PodcastService(db, _test_settings())

    script1, playback1 = await service.generate_script_and_start_playback(
        user_id=fx["user_id"],
        podcast_id=podcast_id,
        target_duration_seconds=300,
        lease_owner="director-1",
        deadline_seconds=3600,
        adapter=_FakePodcastAdapter(),  # type: ignore[arg-type]
    )
    assert script1.status == "ready"
    assert script1.revision == 1
    assert playback1.state == "playing"

    # Segments tersimpan dengan dua agent version berbeda (dua suara).
    segments = (
        (
            await db.execute(
                select(PodcastSegment).where(PodcastSegment.script_version_id == script1.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(segments) == 2
    assert {s.agent_version_id for s in segments} == {
        fx["elean_version"],
        fx["willy_version"],
    }

    # Klik play kedua: script version BARU (revision 2) + session BARU.
    script2, playback2 = await service.generate_script_and_start_playback(
        user_id=fx["user_id"],
        podcast_id=podcast_id,
        target_duration_seconds=300,
        lease_owner="director-1",
        deadline_seconds=3600,
        adapter=_FakePodcastAdapter(),  # type: ignore[arg-type]
    )
    assert script2.revision == 2
    assert script2.id != script1.id
    assert playback2.session_id != playback1.session_id


@requires_db
async def test_play_blocked_until_source_parsed(db: AsyncSession) -> None:
    """Source belum parsed → play ditolak SOURCE_NOT_PARSED (cek status ingestion)."""
    fx = await _fixture(db)
    await _register_script_flow(db)
    service = PodcastService(db, _test_settings())
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Doc")
    await service.add_source(user_id=fx["user_id"], podcast_id=podcast.id, media_id=fx["media_id"])
    with pytest.raises(ConflictError) as error:
        await service.generate_script_and_start_playback(
            user_id=fx["user_id"],
            podcast_id=podcast.id,
            target_duration_seconds=300,
            lease_owner="director-1",
            deadline_seconds=3600,
            adapter=_FakePodcastAdapter(),  # type: ignore[arg-type]
        )
    assert error.value.code == "SOURCE_NOT_PARSED"


@requires_db
async def test_play_propagates_langflow_failure_without_script(db: AsyncSession) -> None:
    """Kegagalan vendor → DependencyUnavailableError; tidak ada script tersimpan."""
    fx = await _fixture(db)
    podcast_id, _ = await _ready_source(db, fx)
    await _register_script_flow(db)
    service = PodcastService(db, _test_settings())
    with pytest.raises(DependencyUnavailableError):
        await service.generate_script_and_start_playback(
            user_id=fx["user_id"],
            podcast_id=podcast_id,
            target_duration_seconds=300,
            lease_owner="director-1",
            deadline_seconds=3600,
            adapter=_FailingPodcastAdapter(),  # type: ignore[arg-type]
        )
    podcasts = await service.get_podcast(user_id=fx["user_id"], podcast_id=podcast_id)
    assert podcasts.current_script_version_id is None


@requires_db
async def test_ingestion_result_marks_source_ready(db: AsyncSession) -> None:
    """Poll completed → parsed + state source_ready + event source_processed."""
    fx = await _fixture(db)
    service = PodcastService(db)
    podcast = await service.create_podcast(user_id=fx["user_id"], title="Doc")
    source = await service.add_source(
        user_id=fx["user_id"], podcast_id=podcast.id, media_id=fx["media_id"]
    )
    updated = await service.apply_ingestion_result(
        source_version_id=source.id, page_count=9, langflow_job_id="lf-job-1"
    )
    assert updated.parse_status == "parsed"
    podcast_after = await service.get_podcast(user_id=fx["user_id"], podcast_id=podcast.id)
    assert podcast_after.state == "source_ready"
    assert podcast_after.generation_job_id == "lf-job-1"
    event = (
        await db.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "podcast.source_processed.v1")
        )
    ).scalar_one_or_none()
    assert event is not None
