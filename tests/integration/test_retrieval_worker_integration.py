"""Integration tests retrieval query path + worker event handlers (Phase 3).

Menutup exit criteria:
- Retrieval: cross-owner tidak pernah terbaca; hanya published + projected
  generation yang dilayani; provenance lengkap
- Worker handler: session_completed → ingestion jobs idempoten (dedupe pada
  delivery ulang); ownership divalidasi ulang dari DB (bukan percaya payload)
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.catalog.models import (
    AiModelConfiguration,
    ProviderCatalog,
)
from temanbule.modules.identity.models import User
from temanbule.modules.knowledge.models import (
    EmbeddingProfile,
    EmbeddingProjection,
    KnowledgeChunk,
)
from temanbule.modules.knowledge.retrieval import RetrievalService
from temanbule.modules.knowledge.services import KnowledgeService
from temanbule.modules.reliability.models import BackgroundJob
from temanbule.platform.errors import NotFoundError
from temanbule.platform.security import new_ulid, sha256_hex
from temanbule.worker.handlers import handle_session_completed

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


async def _memory_fixture(db: AsyncSession) -> dict[str, str]:
    """Dua user masing-masing dengan dokumen memory published."""
    user_a = User(id=new_ulid(), normalized_email=f"ret-a-{new_ulid()[-10:]}@example.com")
    user_b = User(id=new_ulid(), normalized_email=f"ret-b-{new_ulid()[-10:]}@example.com")
    db.add_all([user_a, user_b])
    await db.flush()

    service = KnowledgeService(db)
    doc_a, _ = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash=sha256_hex("a"),
        chunks=["alpha memory A"],
        owner_user_id=user_a.id,
    )
    doc_b, _ = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash=sha256_hex("b"),
        chunks=["beta memory B"],
        owner_user_id=user_b.id,
    )
    return {
        "user_a": user_a.id,
        "user_b": user_b.id,
        "doc_a": doc_a.id,
        "doc_b": doc_b.id,
    }


async def _profile_fixture(db: AsyncSession) -> str:
    suffix = new_ulid()[-10:]
    provider = ProviderCatalog(id=new_ulid(), code=f"emb-{suffix}", status="active")
    db.add(provider)
    await db.flush()
    model = AiModelConfiguration(
        id=new_ulid(),
        provider_id=provider.id,
        identifier=f"emb-m-{suffix}",
        revision=1,
        capabilities='["embedding"]',
        adapter="gemini",
        adapter_version="1",
        status="active",
    )
    db.add(model)
    await db.flush()
    profile = EmbeddingProfile(
        id=new_ulid(),
        provider_id=provider.id,
        model_id=model.id,
        model_revision=1,
        dimension=768,
        document_task_type="RETRIEVAL_DOCUMENT",
        query_task_type="RETRIEVAL_QUERY",
        normalization="l2",
        generation=1,
        status="active",
    )
    db.add(profile)
    await db.flush()
    return profile.id


# --- Retrieval tests ---


@requires_db
async def test_retrieval_owner_scoped_only(db: AsyncSession) -> None:
    fx = await _memory_fixture(db)
    service = RetrievalService(db)

    chunks_a = await service.query_user_memory(user_id=fx["user_a"])
    assert len(chunks_a) == 1
    assert chunks_a[0].text == "alpha memory A"
    assert chunks_a[0].source_type == "conversation_extraction"
    assert chunks_a[0].source_version == "v1"

    chunks_b = await service.query_user_memory(user_id=fx["user_b"])
    assert len(chunks_b) == 1
    assert chunks_b[0].text == "beta memory B"

    # User ketiga tanpa memory → kosong, bukan bocor
    stranger = new_ulid()
    assert await service.query_user_memory(user_id=stranger) == []


@requires_db
async def test_retrieval_excludes_unpublished_and_deleted(db: AsyncSession) -> None:
    fx = await _memory_fixture(db)
    service = KnowledgeService(db)
    # Dokumen draft user_a
    draft, _ = await service.commit_canonical_document(
        scope="agent_knowledge",
        source_type="agent_doc",
        source_id=new_ulid(),
        source_version="v1",
        content_hash="x",
        chunks=["draft chunk"],
        owner_user_id=None,
    )
    draft.publication_state = "draft"
    await db.flush()

    retrieval = RetrievalService(db)
    chunks = await retrieval.query_user_memory(user_id=fx["user_a"], scope="agent_knowledge")
    assert chunks == []


@requires_db
async def test_projected_chunk_requires_projection_state(db: AsyncSession) -> None:
    fx = await _memory_fixture(db)
    profile_id = await _profile_fixture(db)
    retrieval = RetrievalService(db)

    chunk_id = (
        await db.execute(
            select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == fx["doc_a"])
        )
    ).scalar_one()

    # Belum ada projection → 404
    with pytest.raises(NotFoundError):
        await retrieval.get_projected_chunk(
            user_id=fx["user_a"], chunk_id=chunk_id, profile_id=profile_id, generation=1
        )

    # Setelah projected → dilayani
    db.add(
        EmbeddingProjection(
            id=new_ulid(),
            chunk_id=chunk_id,
            source_version="v1",
            profile_id=profile_id,
            generation=1,
            vector_id="vec-1",
            state="projected",
            content_hash="h",
        )
    )
    await db.flush()
    chunk = await retrieval.get_projected_chunk(
        user_id=fx["user_a"], chunk_id=chunk_id, profile_id=profile_id, generation=1
    )
    assert chunk.chunk_id == chunk_id

    # Cross-owner tetap 404 walau projection ada
    with pytest.raises(NotFoundError):
        await retrieval.get_projected_chunk(
            user_id=fx["user_b"], chunk_id=chunk_id, profile_id=profile_id, generation=1
        )


# --- Worker handler tests ---


async def _session_fixture(db: AsyncSession) -> dict[str, str]:
    """Session completed dengan 3 pesan, memakai jalur service nyata."""
    from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
    from temanbule.modules.catalog.plan_selection import PlanSelectionService
    from temanbule.modules.conversations.models import PracticeCategory
    from temanbule.modules.conversations.services import ConversationService

    user = User(id=new_ulid(), normalized_email=f"wh-{new_ulid()[-10:]}@example.com")
    category = PracticeCategory(
        id=new_ulid(), code=f"cat-{new_ulid()[-10:]}", title="Grammar", status="published"
    )
    db.add_all([user, category])
    await db.flush()

    agent = (await db.execute(select(Agent).where(Agent.code == "elean"))).scalar_one_or_none()
    if agent is None:
        agent = Agent(id=new_ulid(), code="elean", display_name="Elean", status="active")
        db.add(agent)
        await db.flush()
    if agent.active_version_id is None:
        version = AgentVersion(id=new_ulid(), agent_id=agent.id, revision=1, status="published")
        db.add(version)
        await db.flush()
        agent.active_version_id = version.id
        await db.flush()

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

    await PlanSelectionService(db).select_plan(user_id=user.id, plan_code="vip")
    conversations = ConversationService(db)
    conversation = await conversations.start_practice_session(
        user_id=user.id, agent_code="elean", category_id=category.id
    )
    for i in range(3):
        await conversations.append_user_message(
            user_id=user.id,
            session_id=conversation.id,
            text=f"message {i}",
            client_key=f"m{i}",
        )
    await conversations.complete_session(user_id=user.id, session_id=conversation.id)
    return {"user_id": user.id, "session_id": conversation.id}


@requires_db
async def test_handler_creates_ingestion_jobs_idempotently(db: AsyncSession) -> None:
    fx = await _session_fixture(db)
    payload = {"session_id": fx["session_id"], "owner_user_id": fx["user_id"]}

    await handle_session_completed(db, payload)
    jobs = (
        (
            await db.execute(
                select(BackgroundJob).where(BackgroundJob.purpose == "conversation_ingestion")
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1  # 3 pesan dalam satu blok range
    job_payload = jobs[0].payload
    assert fx["session_id"] in job_payload

    # Delivery ulang event (at-least-once) tidak menggandakan job
    await handle_session_completed(db, payload)
    count = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.purpose == "conversation_ingestion"
            )
        )
    ).scalar_one()
    assert count == 1


@requires_db
async def test_handler_revalidates_ownership_from_db(db: AsyncSession) -> None:
    """Payload event berbohong tentang owner → ditolak; data otoritatif dari DB."""
    fx = await _session_fixture(db)
    payload = {"session_id": fx["session_id"], "owner_user_id": new_ulid()}
    with pytest.raises(NotFoundError):
        await handle_session_completed(db, payload)

    count = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.purpose == "conversation_ingestion"
            )
        )
    ).scalar_one()
    assert count == 0


@requires_db
async def test_handler_rejects_incomplete_payload(db: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await handle_session_completed(db, {"session_id": "", "owner_user_id": ""})
