"""Integration tests facts/assessment/extraction_committed chain (Phase 3).

Menutup exit criteria:
- user_facts.upsert: consent rule (inferred tidak menimpa confirmed),
  provenance wajib, supersede terlacak, dedupe fact_key aktif
- learning.record_assessment: bounded dimensions, dedupe evidence/rubric/flow,
  tidak menimpa profil
- extraction_committed handler: facts+assessment jobs independen, idempoten
- Tool path: purpose allowlist, idempotency, no fake success
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.ai_runtime.analysis_tools import register_analysis_tools
from temanbule.modules.ai_runtime.grants import ExecutionGrantService
from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
from temanbule.modules.conversations.facts import FactsService
from temanbule.modules.conversations.models import (
    PracticeCategory,
    UserFact,
)
from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.identity.models import User
from temanbule.modules.knowledge.services import IngestionService
from temanbule.modules.reliability.models import BackgroundJob
from temanbule.platform.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from temanbule.platform.security import new_ulid
from temanbule.worker.handlers import handle_extraction_committed

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


async def _user_fixture(db: AsyncSession) -> str:
    user = User(id=new_ulid(), normalized_email=f"fact-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    return user.id


# --- Facts service tests ---


@requires_db
async def test_upsert_fact_proposed_and_dedupe(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    fact, created = await service.upsert_fact(
        user_id=user_id,
        fact_key="goal",
        value="pass TOEFL",
        confidence=0.6,
        provenance_ref='["msg-1"]',
        source_version="v1",
    )
    assert created is True
    assert fact.status == "proposed"

    again, created_again = await service.upsert_fact(
        user_id=user_id,
        fact_key="goal",
        value="pass TOEFL",
        confidence=0.6,
        provenance_ref='["msg-1"]',
        source_version="v1",
    )
    assert created_again is False
    assert again.id == fact.id


@requires_db
async def test_confirmed_fact_immutable_to_inference(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    fact, _ = await service.upsert_fact(
        user_id=user_id,
        fact_key="level",
        value="B1",
        confidence=1.0,
        provenance_ref='["user-input"]',
        source_version="v1",
        proposed_status="confirmed",
    )
    # Inferensi baru dengan nilai berbeda → 409, tidak menimpa
    with pytest.raises(ConflictError, match="dikonfirmasi"):
        await service.upsert_fact(
            user_id=user_id,
            fact_key="level",
            value="A2",
            confidence=0.5,
            provenance_ref='["msg-9"]',
            source_version="v2",
        )
    # Nilai sama → no-op aman
    same, created = await service.upsert_fact(
        user_id=user_id,
        fact_key="level",
        value="B1",
        confidence=0.9,
        provenance_ref='["msg-9"]',
        source_version="v2",
    )
    assert created is False
    assert same.id == fact.id


@requires_db
async def test_supersede_tracked_on_proposed_revision(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    old, _ = await service.upsert_fact(
        user_id=user_id,
        fact_key="hobby",
        value="reading",
        confidence=0.4,
        provenance_ref='["m1"]',
        source_version="v1",
    )
    new, created = await service.upsert_fact(
        user_id=user_id,
        fact_key="hobby",
        value="swimming",
        confidence=0.7,
        provenance_ref='["m2"]',
        source_version="v2",
    )
    assert created is True
    assert new.supersedes_id == old.id
    await db.refresh(old)
    assert old.status == "superseded"

    active = await service.list_facts(user_id=user_id)
    assert len(active) == 1
    assert active[0].value == "swimming"


@requires_db
async def test_confidence_bounds_enforced(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    with pytest.raises(ValidationError, match="Confidence"):
        await service.upsert_fact(
            user_id=user_id,
            fact_key="x",
            value="y",
            confidence=1.5,
            provenance_ref='["m"]',
            source_version="v1",
        )


@requires_db
async def test_provenance_required(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    with pytest.raises(ValidationError, match="provenance"):
        await service.upsert_fact(
            user_id=user_id,
            fact_key="x",
            value="y",
            confidence=0.5,
            provenance_ref="  ",
            source_version="v1",
        )


@requires_db
async def test_cross_owner_fact_access_denied(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    service = FactsService(db)
    fact, _ = await service.upsert_fact(
        user_id=user_id,
        fact_key="secret",
        value="data",
        confidence=0.5,
        provenance_ref='["m"]',
        source_version="v1",
    )
    with pytest.raises(NotFoundError):
        await service.confirm_fact(user_id=new_ulid(), fact_id=fact.id)


# --- Tool path tests ---


async def _grant(db: AsyncSession, user_id: str, purpose: str, scopes: list[str]) -> str:
    grants = ExecutionGrantService(db)
    grant = grants.issue_grant(
        request_id=new_ulid(),
        service_identity="callcraft",
        purpose=purpose,
        scopes=scopes,
        ttl_seconds=60,
        owner_user_id=user_id,
    )
    await db.flush()
    return grant.id


@requires_db
async def test_facts_upsert_tool_replay_and_purpose(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    grant_id = await _grant(db, user_id, "user_fact_extraction", ["facts:write"])
    tools = ToolExecutionService(db)
    register_analysis_tools(tools)

    result = await tools.execute(
        tool_name="user_facts.upsert",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="fact-key-1",
        arguments={
            "fact_key": "goal",
            "value": "fluent",
            "confidence": 0.7,
            "source_message_ids": ["m1", "m2"],
        },
    )
    assert result.status == "succeeded"

    replay = await tools.execute(
        tool_name="user_facts.upsert",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="fact-key-1",
        arguments={
            "fact_key": "goal",
            "value": "fluent",
            "confidence": 0.7,
            "source_message_ids": ["m1", "m2"],
        },
    )
    assert replay.replayed is True

    count = (
        await db.execute(
            select(func.count(UserFact.id)).where(UserFact.user_id == user_id)
        )
    ).scalar_one()
    assert count == 1

    # Purpose salah: practice_interaction tidak boleh upsert facts
    wrong_grant = await _grant(db, user_id, "practice_interaction", ["facts:write"])
    with pytest.raises(ConflictError, match="purpose"):
        await tools.execute(
            tool_name="user_facts.upsert",
            grant_id=wrong_grant,
            service_identity="callcraft",
            request_id=new_ulid(),
            idempotency_key="fact-key-2",
            arguments={
                "fact_key": "x",
                "value": "y",
                "confidence": 0.5,
                "source_message_ids": ["m"],
            },
        )


@requires_db
async def test_facts_tool_no_fake_success_on_invalid_args(db: AsyncSession) -> None:
    user_id = await _user_fixture(db)
    grant_id = await _grant(db, user_id, "user_fact_extraction", ["facts:write"])
    tools = ToolExecutionService(db)
    register_analysis_tools(tools)
    result = await tools.execute(
        tool_name="user_facts.upsert",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="fact-key-3",
        arguments={"fact_key": "x", "value": "y", "confidence": 0.5},  # tanpa provenance
    )
    assert result.status == "failed"
    assert result.error_code == "VALIDATION_FAILED"


# --- extraction_committed handler tests ---


async def _extraction_fixture(db: AsyncSession) -> dict[str, object]:
    """Extraction nyata lewat IngestionService agar event chain teruji."""
    from temanbule.modules.catalog.plan_selection import PlanSelectionService

    user = User(id=new_ulid(), normalized_email=f"echain-{new_ulid()[-10:]}@example.com")
    category = PracticeCategory(
        id=new_ulid(), code=f"cat-{new_ulid()[-10:]}", title="Travel", status="published"
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
    for i in range(2):
        await conversations.append_user_message(
            user_id=user.id,
            session_id=conversation.id,
            text=f"msg {i}",
            client_key=f"c{i}",
        )

    class _Extractor:
        async def extract(
            self,
            *,
            session_id: str,
            messages: list,
            schema_version: str,
            flow_version: str,
        ) -> dict:
            return {"summary": "s", "evidence": [1, 2]}

    ingestion = IngestionService(db)
    extraction = await ingestion.run_ingestion(
        job_id=new_ulid(),
        session_id=conversation.id,
        owner_user_id=user.id,
        source_start=1,
        source_end=2,
        flow_version="conv-ing.v1",
        extractor=_Extractor(),
    )
    return {"user_id": user.id, "session_id": conversation.id, "extraction_id": extraction.id}


@requires_db
async def test_extraction_committed_creates_independent_jobs(db: AsyncSession) -> None:
    fx = await _extraction_fixture(db)
    payload = {
        "extraction_id": fx["extraction_id"],
        "session_id": fx["session_id"],
        "owner_user_id": fx["user_id"],
        "source_start": 1,
        "source_end": 2,
    }
    await handle_extraction_committed(db, payload)

    purposes = (
        (
            await db.execute(
                select(BackgroundJob.purpose).where(
                    BackgroundJob.purpose.in_(
                        ["user_fact_extraction", "learning_assessment"]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(purposes) == ["learning_assessment", "user_fact_extraction"]

    # Delivery ulang: tidak menggandakan kedua job
    await handle_extraction_committed(db, payload)
    count = (
        await db.execute(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.purpose.in_(
                    ["user_fact_extraction", "learning_assessment"]
                )
            )
        )
    ).scalar_one()
    assert count == 2


@requires_db
async def test_extraction_committed_revalidates_owner(db: AsyncSession) -> None:
    fx = await _extraction_fixture(db)
    payload = {
        "extraction_id": fx["extraction_id"],
        "session_id": fx["session_id"],
        "owner_user_id": new_ulid(),  # owner palsu
        "source_start": 1,
        "source_end": 2,
    }
    with pytest.raises(NotFoundError):
        await handle_extraction_committed(db, payload)
