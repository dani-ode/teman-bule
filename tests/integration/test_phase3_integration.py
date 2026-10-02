"""Integration tests Phase 3 text slice pada PostgreSQL nyata.

Menutup exit criteria Phase 3 (bagian domain/backend):
- IDOR: cross-owner session/vocabulary/tool access → 404/Forbidden
- replay: client_key dan tool idempotency → tanpa duplikasi
- no fake tool success: domain failure → status failed, bukan klaim sukses
- payload conflict: key sama + hash beda → 409
- append-only messages: DB trigger menolak update
- tool purpose allowlist: salah purpose → ditolak
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.ai_runtime.grants import ExecutionGrantService
from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.ai_runtime.vocabulary_tools import register_vocabulary_tools
from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    Plan,
    PlanPolicyVersion,
)
from temanbule.modules.conversations.models import (
    PracticeCategory,
    PracticeSession,
)
from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.identity.models import User
from temanbule.modules.vocabulary.models import VocabularyEntry
from temanbule.modules.vocabulary.services import VocabularyService, normalize_lemma
from temanbule.platform.errors import (
    ConflictError,
    IdempotencyConflictError,
    NotFoundError,
)
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


async def _base_fixture(db: AsyncSession) -> dict[str, str]:
    user = User(id=new_ulid(), normalized_email=f"p3-{new_ulid()[-10:]}@example.com")
    category = PracticeCategory(
        id=new_ulid(),
        code=f"cat-{new_ulid()[-10:]}",
        title="Daily Conversation",
        status="published",
    )
    db.add_all([user, category])
    await db.flush()

    await _ensure_agent(db, "elean", "Elean")

    existing_plan = (
        await db.execute(select(Plan).where(Plan.code == "vip"))
    ).scalar_one_or_none()
    if existing_plan is None:
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
    return {"user_id": user.id, "category_id": category.id, "agent_code": "elean"}


async def _ensure_agent(db: AsyncSession, code: str, display_name: str) -> Agent:
    """Idempotent: pastikan agent punya versi aktif published."""
    agent = (
        await db.execute(select(Agent).where(Agent.code == code))
    ).scalar_one_or_none()
    if agent is None:
        agent = Agent(id=new_ulid(), code=code, display_name=display_name, status="active")
        db.add(agent)
        await db.flush()
    if agent.active_version_id is None:
        version = AgentVersion(id=new_ulid(), agent_id=agent.id, revision=1, status="published")
        db.add(version)
        await db.flush()
        agent.active_version_id = version.id
        await db.flush()
    return agent


def _tools(db: AsyncSession) -> ToolExecutionService:
    service = ToolExecutionService(db)
    register_vocabulary_tools(service)
    return service


# --- Conversation tests ---


@requires_db
async def test_session_and_message_flow(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    assert conversation.kind == "chat"
    assert conversation.state == "active"

    message = await service.append_user_message(
        user_id=fx["user_id"],
        session_id=conversation.id,
        text="Hello, I want to practice English.",
        client_key="client-msg-1",
    )
    assert message.sequence == 1
    assert message.role == "user"

    # Ambil agent_version_id nyata dari practice session (FK wajib valid)
    practice = (
        await db.execute(
            select(PracticeSession).where(PracticeSession.session_id == conversation.id)
        )
    ).scalar_one()
    agent_msg = await service.append_agent_message(
        session_id=conversation.id,
        owner_user_id=fx["user_id"],
        agent_version_id=practice.agent_version_id,
        text="Great! Let's start with introductions.",
    )
    assert agent_msg.sequence == 2

    messages = await service.list_messages(user_id=fx["user_id"], session_id=conversation.id)
    assert [m.sequence for m in messages] == [1, 2]


@requires_db
async def test_client_key_replay_returns_same_message(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    first = await service.append_user_message(
        user_id=fx["user_id"],
        session_id=conversation.id,
        text="Replay me",
        client_key="dup-key",
    )
    replayed = await service.append_user_message(
        user_id=fx["user_id"],
        session_id=conversation.id,
        text="Replay me",
        client_key="dup-key",
    )
    assert first.id == replayed.id
    messages = await service.list_messages(user_id=fx["user_id"], session_id=conversation.id)
    assert len(messages) == 1


@requires_db
async def test_cross_owner_session_access_denied(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    other_user = User(id=new_ulid(), normalized_email=f"idor-{new_ulid()[-10:]}@example.com")
    db.add(other_user)
    await db.flush()

    with pytest.raises(NotFoundError):
        await service.get_owned_session(user_id=other_user.id, session_id=conversation.id)
    with pytest.raises(NotFoundError):
        await service.append_user_message(
            user_id=other_user.id,
            session_id=conversation.id,
            text="IDOR attempt",
            client_key=None,
        )
    with pytest.raises(NotFoundError):
        await service.list_messages(user_id=other_user.id, session_id=conversation.id)


@requires_db
async def test_list_practice_sessions_filters(db: AsyncSession) -> None:
    """list_practice_sessions: filter kategori/agent/state + owner-scoped + urut terbaru."""
    fx = await _base_fixture(db)
    await _ensure_agent(db, "willy", "Willy")
    service = ConversationService(db)

    # Kategori kedua untuk membedakan filter category_id.
    category_b = PracticeCategory(
        id=new_ulid(), code=f"cat-{new_ulid()[-10:]}", title="Business", status="published"
    )
    db.add(category_b)
    await db.flush()

    # Tiga session milik user: elean/catA, willy/catA, elean/catB.
    s_elean_a = await service.start_practice_session(
        user_id=fx["user_id"], agent_code="elean", category_id=fx["category_id"]
    )
    s_willy_a = await service.start_practice_session(
        user_id=fx["user_id"], agent_code="willy", category_id=fx["category_id"]
    )
    s_elean_b = await service.start_practice_session(
        user_id=fx["user_id"], agent_code="elean", category_id=category_b.id
    )
    # Session milik user lain: tidak boleh ikut (owner-scoped).
    other = User(id=new_ulid(), normalized_email=f"other-{new_ulid()[-10:]}@example.com")
    db.add(other)
    await db.flush()
    from temanbule.modules.catalog.plan_selection import PlanSelectionService

    await PlanSelectionService(db).select_plan(user_id=other.id, plan_code="vip")
    s_other = await service.start_practice_session(
        user_id=other.id, agent_code="elean", category_id=fx["category_id"]
    )

    # Tanpa filter: hanya 3 milik user, urut started_at desc (terakhir dibuat duluan).
    all_rows = await service.list_practice_sessions(user_id=fx["user_id"])
    assert [c.id for c, _, _ in all_rows] == [s_elean_b.id, s_willy_a.id, s_elean_a.id]
    assert s_other.id not in [c.id for c, _, _ in all_rows]

    # Filter agent_code: resolve elean/willy via join.
    elean_rows = await service.list_practice_sessions(user_id=fx["user_id"], agent_code="elean")
    assert {c.id for c, _, _ in elean_rows} == {s_elean_a.id, s_elean_b.id}
    assert all(code == "elean" for _, _, code in elean_rows)

    # Filter category_id.
    cat_b_rows = await service.list_practice_sessions(user_id=fx["user_id"], category_id=category_b.id)
    assert [c.id for c, _, _ in cat_b_rows] == [s_elean_b.id]

    # Filter gabungan agent + kategori.
    combo = await service.list_practice_sessions(
        user_id=fx["user_id"], agent_code="elean", category_id=fx["category_id"]
    )
    assert [c.id for c, _, _ in combo] == [s_elean_a.id]

    # Filter state setelah complete.
    await service.complete_session(user_id=fx["user_id"], session_id=s_elean_a.id)
    completed = await service.list_practice_sessions(user_id=fx["user_id"], state="completed")
    assert [c.id for c, _, _ in completed] == [s_elean_a.id]
    active = await service.list_practice_sessions(user_id=fx["user_id"], state="active")
    assert {c.id for c, _, _ in active} == {s_elean_b.id, s_willy_a.id}


@requires_db
async def test_completed_session_rejects_new_messages(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    await service.complete_session(user_id=fx["user_id"], session_id=conversation.id)
    with pytest.raises(ConflictError, match="tidak aktif"):
        await service.append_user_message(
            user_id=fx["user_id"],
            session_id=conversation.id,
            text="too late",
            client_key=None,
        )


@requires_db
async def test_complete_session_emits_outbox_event_atomically(db: AsyncSession) -> None:
    """complete_session mencatat conversation.session_completed.v1 di outbox
    dalam transaksi yang sama; complete ulang tidak menggandakan event."""
    from temanbule.modules.reliability.models import OutboxEvent

    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    await service.append_user_message(
        user_id=fx["user_id"], session_id=conversation.id, text="hi", client_key=None
    )

    await service.complete_session(user_id=fx["user_id"], session_id=conversation.id)
    events = (
        (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "conversation_session",
                    OutboxEvent.aggregate_id == conversation.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == "conversation.session_completed.v1"
    assert events[0].aggregate_version == 1
    assert events[0].published_at is None

    # Complete ulang: no-op, tidak ada event kedua
    await service.complete_session(user_id=fx["user_id"], session_id=conversation.id)
    events_after = (
        (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "conversation_session",
                    OutboxEvent.aggregate_id == conversation.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events_after) == 1


@requires_db
async def test_messages_append_only_enforced(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = ConversationService(db)
    conversation = await service.start_practice_session(
        user_id=fx["user_id"], agent_code=fx["agent_code"], category_id=fx["category_id"]
    )
    message = await service.append_user_message(
        user_id=fx["user_id"],
        session_id=conversation.id,
        text="original",
        client_key=None,
    )
    message.text = "tampered"
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append_only_table"):
        await db.flush()
    await db.rollback()


# --- Vocabulary tests ---


@requires_db
async def test_vocabulary_save_upsert_owner_scoped(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = VocabularyService(db)
    entry, created = await service.save_entry(
        user_id=fx["user_id"], lemma="  Serendipity ", language="en", definition="kebetulan"
    )
    assert created is True
    assert entry.normalized_lemma == "serendipity"

    again, created_again = await service.save_entry(
        user_id=fx["user_id"], lemma="SERENDIPITY", language="en"
    )
    assert created_again is False
    assert again.id == entry.id
    # Definition tidak ditimpa bila sudah ada
    assert again.definition == "kebetulan"


@requires_db
async def test_vocabulary_cross_owner_denied(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = VocabularyService(db)
    entry, _ = await service.save_entry(user_id=fx["user_id"], lemma="apple", language="en")
    other_id = new_ulid()
    with pytest.raises(NotFoundError):
        await service.get_entry(user_id=other_id, entry_id=entry.id)
    with pytest.raises(NotFoundError):
        await service.update_status(
            user_id=other_id, entry_id=entry.id, target_state="learning"
        )


@requires_db
async def test_vocabulary_invalid_state_transition(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    service = VocabularyService(db)
    entry, _ = await service.save_entry(user_id=fx["user_id"], lemma="banana", language="en")
    with pytest.raises(ConflictError, match="Transisi"):
        await service.update_status(
            user_id=fx["user_id"], entry_id=entry.id, target_state="mastered"
        )


def test_normalize_lemma_deterministic() -> None:
    assert normalize_lemma("  Hello   World ") == "hello world"
    assert normalize_lemma("CAFÉ") == "café"
    assert normalize_lemma("　") == ""


# --- Tool execution tests (jalur CallCraft internal) ---


async def _issue_practice_grant(db: AsyncSession, user_id: str) -> str:
    grants = ExecutionGrantService(db)
    grant = grants.issue_grant(
        request_id=new_ulid(),
        service_identity="callcraft",
        purpose="practice_interaction",
        scopes=["vocabulary:write", "vocabulary:read"],
        ttl_seconds=60,
        owner_user_id=user_id,
    )
    await db.flush()
    return grant.id


@requires_db
async def test_tool_save_and_replay_identical(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    grant_id = await _issue_practice_grant(db, fx["user_id"])
    tools = _tools(db)

    result = await tools.execute(
        tool_name="vocabulary.save",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="tool-key-1",
        arguments={"lemma": "resilient", "language": "en"},
    )
    assert result.status == "succeeded"
    assert result.result is not None
    assert result.result["created"] is True

    replayed = await tools.execute(
        tool_name="vocabulary.save",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="tool-key-1",
        arguments={"lemma": "resilient", "language": "en"},
    )
    assert replayed.replayed is True
    assert replayed.result == result.result

    entries = (
        (
            await db.execute(
                select(VocabularyEntry).where(VocabularyEntry.user_id == fx["user_id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 1  # replay tidak menggandakan


@requires_db
async def test_tool_same_key_different_payload_409(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    grant_id = await _issue_practice_grant(db, fx["user_id"])
    tools = _tools(db)
    await tools.execute(
        tool_name="vocabulary.save",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="tool-key-2",
        arguments={"lemma": "apple", "language": "en"},
    )
    with pytest.raises(IdempotencyConflictError):
        await tools.execute(
            tool_name="vocabulary.save",
            grant_id=grant_id,
            service_identity="callcraft",
            request_id=new_ulid(),
            idempotency_key="tool-key-2",
            arguments={"lemma": "orange", "language": "en"},
        )


@requires_db
async def test_tool_failure_no_fake_success(db: AsyncSession) -> None:
    """Domain failure → status failed dengan stable code, bukan klaim sukses."""
    fx = await _base_fixture(db)
    grant_id = await _issue_practice_grant(db, fx["user_id"])
    tools = _tools(db)
    result = await tools.execute(
        tool_name="vocabulary.save",
        grant_id=grant_id,
        service_identity="callcraft",
        request_id=new_ulid(),
        idempotency_key="tool-key-3",
        arguments={"lemma": "   ", "language": "en"},  # lemma kosong → ValidationError
    )
    assert result.status == "failed"
    assert result.error_code == "VALIDATION_FAILED"
    assert result.result is None


@requires_db
async def test_tool_wrong_purpose_denied(db: AsyncSession) -> None:
    """vocabulary.save tidak boleh dipanggil dari purpose learning_assistance."""
    fx = await _base_fixture(db)
    grants = ExecutionGrantService(db)
    grant = grants.issue_grant(
        request_id=new_ulid(),
        service_identity="callcraft",
        purpose="learning_assistance",  # allowlist hanya learning.get_progress
        scopes=["vocabulary:write", "learning:read"],
        ttl_seconds=60,
        owner_user_id=fx["user_id"],
    )
    await db.flush()
    tools = _tools(db)
    with pytest.raises(ConflictError, match="purpose"):
        await tools.execute(
            tool_name="vocabulary.save",
            grant_id=grant.id,
            service_identity="callcraft",
            request_id=new_ulid(),
            idempotency_key="tool-key-4",
            arguments={"lemma": "sneaky", "language": "en"},
        )


@requires_db
async def test_tool_missing_scope_denied(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    grants = ExecutionGrantService(db)
    grant = grants.issue_grant(
        request_id=new_ulid(),
        service_identity="callcraft",
        purpose="practice_interaction",
        scopes=["vocabulary:read"],  # tanpa write
        ttl_seconds=60,
        owner_user_id=fx["user_id"],
    )
    await db.flush()
    tools = _tools(db)
    from temanbule.platform.errors import ForbiddenError

    with pytest.raises(ForbiddenError, match="Scope"):
        await tools.execute(
            tool_name="vocabulary.save",
            grant_id=grant.id,
            service_identity="callcraft",
            request_id=new_ulid(),
            idempotency_key="tool-key-5",
            arguments={"lemma": "scope-test", "language": "en"},
        )


@requires_db
async def test_tool_mutation_without_key_rejected(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    grant_id = await _issue_practice_grant(db, fx["user_id"])
    tools = _tools(db)
    from temanbule.platform.errors import ValidationError

    with pytest.raises(ValidationError, match="idempotency_key"):
        await tools.execute(
            tool_name="vocabulary.save",
            grant_id=grant_id,
            service_identity="callcraft",
            request_id=new_ulid(),
            idempotency_key=None,
            arguments={"lemma": "no-key", "language": "en"},
        )
