"""Integration tests ingestion + dual embedding dispatch (Phase 3).

Menutup exit criteria Phase 3 (jalur background deterministik):
- Extraction canonical idempoten per range+schema+flow (replay tidak memanggil
  processor ulang, tidak menggandakan baris)
- Dual dispatch deterministik: satu job per (chunk, profile, generation)
- Single-branch failure retry: branch gagal diulang, branch sukses TIDAK
- Canonical commit idempoten per source+version
- user_memory wajib owner (IDOR-by-construction)

ExtractionPort/EmbeddingPort di sini TEST DOUBLE berlabel jelas; bukan bukti
kompatibilitas Langflow/provider (DEC-09/10 pending).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    AiModelConfiguration,
    Plan,
    PlanPolicyVersion,
    ProviderCatalog,
)
from temanbule.modules.conversations.models import (
    ConversationExtraction,
    PracticeCategory,
    PracticeSession,
)
from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.identity.models import User
from temanbule.modules.knowledge.models import (
    EmbeddingProfile,
    EmbeddingProjection,
    KnowledgeChunk,
)
from temanbule.modules.knowledge.services import (
    IngestionService,
    KnowledgeService,
)
from temanbule.modules.reliability.models import BackgroundJob
from temanbule.platform.errors import ConflictError, ValidationError
from temanbule.platform.security import new_ulid, sha256_hex

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")


class FakeExtractor:
    """TEST DOUBLE — bukan bukti kompatibilitas Langflow (DEC-10 pending)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        schema_version: str,
        flow_version: str,
    ) -> dict[str, Any]:
        self.calls.append({"session_id": session_id, "count": len(messages)})
        return {
            "summary": f"summary of {len(messages)} messages",
            "evidence": [m["sequence"] for m in messages],
        }


class FakeEmbedder:
    """TEST DOUBLE — bukan bukti kompatibilitas provider (DEC-09 pending)."""

    def __init__(self, fail_profile_ids: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_profile_ids = fail_profile_ids or set()

    async def embed(self, *, chunk_text: str, profile: EmbeddingProfile) -> str:
        self.calls.append(profile.id)
        if profile.id in self.fail_profile_ids:
            from temanbule.platform.errors import DependencyUnavailableError

            raise DependencyUnavailableError("provider down (test double)")
        return f"vec-{profile.id[:8]}-{sha256_hex(chunk_text)[:12]}"


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


async def _conversation_fixture(db: AsyncSession) -> dict[str, Any]:
    user = User(id=new_ulid(), normalized_email=f"ing-{new_ulid()[-10:]}@example.com")
    category = PracticeCategory(
        id=new_ulid(), code=f"cat-{new_ulid()[-10:]}", title="Free Talk", status="published"
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

    from temanbule.modules.catalog.plan_selection import PlanSelectionService

    await PlanSelectionService(db).select_plan(user_id=user.id, plan_code="vip")

    conversations = ConversationService(db)
    conversation = await conversations.start_practice_session(
        user_id=user.id, agent_code="elean", category_id=category.id
    )
    for i, text in enumerate(["Hi there", "I want to learn", "Thank you"]):
        await conversations.append_user_message(
            user_id=user.id, session_id=conversation.id, text=text, client_key=f"k{i}"
        )
    practice = (
        await db.execute(
            select(PracticeSession).where(PracticeSession.session_id == conversation.id)
        )
    ).scalar_one()
    return {
        "user_id": user.id,
        "session_id": conversation.id,
        "agent_version_id": practice.agent_version_id,
    }


async def _profile_fixture(db: AsyncSession) -> list[str]:
    """Dua profile aktif (mensimulasikan gemini + openai)."""
    suffix = new_ulid()[-10:]
    provider_a = ProviderCatalog(id=new_ulid(), code=f"gem-{suffix}", status="active")
    provider_b = ProviderCatalog(id=new_ulid(), code=f"oai-{suffix}", status="active")
    db.add_all([provider_a, provider_b])
    await db.flush()
    profile_ids: list[str] = []
    for provider, task in ((provider_a, "RETRIEVAL_DOCUMENT"), (provider_b, "document")):
        model = AiModelConfiguration(
            id=new_ulid(),
            provider_id=provider.id,
            identifier=f"emb-{suffix}",
            revision=1,
            capabilities='["embedding"]',
            adapter=provider.code.split("-")[0],
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
            document_task_type=task,
            query_task_type="query",
            normalization="l2",
            generation=1,
            status="active",
        )
        db.add(profile)
        await db.flush()
        profile_ids.append(profile.id)
    return profile_ids


@requires_db
async def test_ingestion_idempotent_replay(db: AsyncSession) -> None:
    fx = await _conversation_fixture(db)
    service = IngestionService(db)
    extractor = FakeExtractor()

    first = await service.run_ingestion(
        job_id=new_ulid(),
        session_id=fx["session_id"],
        owner_user_id=fx["user_id"],
        source_start=1,
        source_end=2,
        flow_version="conv-ing.v1",
        extractor=extractor,
    )
    assert len(extractor.calls) == 1

    replayed = await service.run_ingestion(
        job_id=new_ulid(),
        session_id=fx["session_id"],
        owner_user_id=fx["user_id"],
        source_start=1,
        source_end=2,
        flow_version="conv-ing.v1",
        extractor=extractor,
    )
    assert replayed.id == first.id
    assert len(extractor.calls) == 1  # processor tidak dipanggil ulang

    count = (
        await db.execute(
            select(func.count(ConversationExtraction.id)).where(
                ConversationExtraction.session_id == fx["session_id"]
            )
        )
    ).scalar_one()
    assert count == 1


@requires_db
async def test_ingestion_rejects_invalid_range_and_owner(db: AsyncSession) -> None:
    fx = await _conversation_fixture(db)
    service = IngestionService(db)
    extractor = FakeExtractor()

    with pytest.raises(ValidationError):
        await service.run_ingestion(
            job_id=new_ulid(),
            session_id=fx["session_id"],
            owner_user_id=fx["user_id"],
            source_start=3,
            source_end=1,
            flow_version="conv-ing.v1",
            extractor=extractor,
        )
    from temanbule.platform.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.run_ingestion(
            job_id=new_ulid(),
            session_id=fx["session_id"],
            owner_user_id=new_ulid(),  # cross-owner
            source_start=1,
            source_end=2,
            flow_version="conv-ing.v1",
            extractor=extractor,
        )
    with pytest.raises(ValidationError, match="kosong"):
        await service.run_ingestion(
            job_id=new_ulid(),
            session_id=fx["session_id"],
            owner_user_id=fx["user_id"],
            source_start=99,
            source_end=100,
            flow_version="conv-ing.v1",
            extractor=extractor,
        )


@requires_db
async def test_canonical_commit_idempotent(db: AsyncSession) -> None:
    fx = await _conversation_fixture(db)
    service = KnowledgeService(db)
    content_hash = sha256_hex("canonical-content")

    document, created = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash=content_hash,
        chunks=["chunk one", "chunk two"],
        owner_user_id=fx["user_id"],
    )
    assert created is True

    again, created_again = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=document.source_id,
        source_version="v1",
        content_hash=content_hash,
        chunks=["chunk one", "chunk two"],
        owner_user_id=fx["user_id"],
    )
    assert created_again is False
    assert again.id == document.id

    chunk_count = (
        await db.execute(
            select(func.count(KnowledgeChunk.id)).where(
                KnowledgeChunk.document_id == document.id
            )
        )
    ).scalar_one()
    assert chunk_count == 2


@requires_db
async def test_user_memory_requires_owner(db: AsyncSession) -> None:
    service = KnowledgeService(db)
    with pytest.raises(ValidationError, match="owner"):
        await service.commit_canonical_document(
            scope="user_memory",
            source_type="conversation_extraction",
            source_id=new_ulid(),
            source_version="v1",
            content_hash="x",
            chunks=["a"],
            owner_user_id=None,
        )


@requires_db
async def test_dual_dispatch_creates_one_job_per_branch(db: AsyncSession) -> None:
    fx = await _conversation_fixture(db)
    profile_ids = await _profile_fixture(db)
    service = KnowledgeService(db)
    document, _ = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash=sha256_hex("content"),
        chunks=["alpha", "beta"],
        owner_user_id=fx["user_id"],
    )

    jobs = await service.dispatch_dual_embedding(document_id=document.id)
    # 2 chunks × 2 profiles = 4 branch jobs
    assert len(jobs) == 4

    # Dispatch ulang tidak menggandakan (dedupe_key)
    jobs_again = await service.dispatch_dual_embedding(document_id=document.id)
    assert jobs_again == []

    job_count = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.purpose == "embedding_projection"
            )
        )
    ).scalar_one()
    assert job_count == 4
    await db.refresh(document)
    assert document.indexing_state == "partial"
    assert len(profile_ids) == 2


@requires_db
async def test_dispatch_without_active_profile_fails(db: AsyncSession) -> None:
    fx = await _conversation_fixture(db)
    service = KnowledgeService(db)
    document, _ = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash="x",
        chunks=["a"],
        owner_user_id=fx["user_id"],
    )
    with pytest.raises(ConflictError, match="profile aktif"):
        await service.dispatch_dual_embedding(document_id=document.id)


@requires_db
async def test_single_branch_failure_retry(db: AsyncSession) -> None:
    """Satu branch gagal: hanya branch itu yang diulang; sukses tidak diulang."""
    fx = await _conversation_fixture(db)
    profile_ids = await _profile_fixture(db)
    service = KnowledgeService(db)
    document, _ = await service.commit_canonical_document(
        scope="user_memory",
        source_type="conversation_extraction",
        source_id=new_ulid(),
        source_version="v1",
        content_hash=sha256_hex("content"),
        chunks=["only chunk"],
        owner_user_id=fx["user_id"],
    )
    jobs = await service.dispatch_dual_embedding(document_id=document.id)
    assert len(jobs) == 2  # 1 chunk × 2 profiles

    failing_profile = profile_ids[0]
    embedder = FakeEmbedder(fail_profile_ids={failing_profile})

    success_jobs = [j for j in jobs if failing_profile not in j.dedupe_key]
    failing_jobs = [j for j in jobs if failing_profile in j.dedupe_key]
    assert len(success_jobs) == 1 and len(failing_jobs) == 1

    # Branch sukses berjalan
    await service.run_embedding_job(job=success_jobs[0], embedder=embedder)
    # Branch gagal melempar (job tetap nonterminal untuk retry)
    from temanbule.platform.errors import DependencyUnavailableError

    with pytest.raises(DependencyUnavailableError):
        await service.run_embedding_job(job=failing_jobs[0], embedder=embedder)

    # Verifikasi state: satu projected, satu masih pending
    projections = (
        (
            await db.execute(
                select(EmbeddingProjection).where(
                    EmbeddingProjection.chunk_id == (
                        await db.execute(
                            select(KnowledgeChunk.id).where(
                                KnowledgeChunk.document_id == document.id
                            )
                        )
                    ).scalar_one()
                )
            )
        )
        .scalars()
        .all()
    )
    states = {p.profile_id: p.state for p in projections}
    assert states[profile_ids[1]] == "projected"
    assert states[profile_ids[0]] == "pending"

    # Re-run branch sukses = no-op (tidak memanggil provider ulang)
    calls_before = len(embedder.calls)
    await service.run_embedding_job(job=success_jobs[0], embedder=embedder)
    assert len(embedder.calls) == calls_before

    # Retry branch gagal dengan provider pulih → sukses
    recovered = FakeEmbedder()
    await service.run_embedding_job(job=failing_jobs[0], embedder=recovered)
    projections_after = (
        (
            await db.execute(
                select(EmbeddingProjection).where(
                    EmbeddingProjection.chunk_id == (
                        await db.execute(
                            select(KnowledgeChunk.id).where(
                                KnowledgeChunk.document_id == document.id
                            )
                        )
                    ).scalar_one()
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(p.state == "projected" for p in projections_after)
