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

from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
from temanbule.modules.identity.models import User
from temanbule.modules.media.services import MediaService
from temanbule.modules.podcasts.services import PodcastService
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

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
