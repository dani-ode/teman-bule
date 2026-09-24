"""Integration tests Phase 6: call admission, fencing, turns, graceful end.

Menutup exit criteria Phase 6 (state otoritatif backend):
- Admission dengan lease + fencing token monotonic
- Stale writer (token lama/owner salah) ditolak — crash-safe
- Turns sequence atomik; barge-in menandai interrupted, tidak menghapus
- Graceful end idempoten dengan end_reason eksplisit (low_balance)
- Cross-owner denied; consent_version wajib
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.calls.services import CallService
from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
from temanbule.modules.identity.models import User
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
    user = User(id=new_ulid(), normalized_email=f"call-{new_ulid()[-10:]}@example.com")
    db.add(user)
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
    return {"user_id": user.id}


@requires_db
async def test_create_call_requires_consent_and_valid_mode(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    with pytest.raises(ValidationError, match="Mode"):
        await service.create_call(
            user_id=fx["user_id"], agent_code="elean", mode="hologram", consent_version="c1"
        )
    with pytest.raises(ValidationError, match="consent"):
        await service.create_call(
            user_id=fx["user_id"], agent_code="elean", mode="voice", consent_version=" "
        )


@requires_db
async def test_admission_lease_and_fencing(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    call = await service.create_call(
        user_id=fx["user_id"], agent_code="elean", mode="voice", consent_version="c1"
    )
    admitted = await service.admit_call(
        user_id=fx["user_id"], session_id=call.session_id,
        lease_owner="worker-1", lease_seconds=3600,
    )
    assert admitted.fencing_token == 1
    assert admitted.lease_owner == "worker-1"

    activated = await service.activate_call(
        user_id=fx["user_id"], session_id=call.session_id,
        fencing_token=1, lease_owner="worker-1",
    )
    assert activated.state == "active"


@requires_db
async def test_stale_writer_rejected(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    call = await service.create_call(
        user_id=fx["user_id"], agent_code="elean", mode="voice", consent_version="c1"
    )
    await service.admit_call(
        user_id=fx["user_id"], session_id=call.session_id,
        lease_owner="worker-1", lease_seconds=3600,
    )
    # Simulasi worker crash → lease diambil alih worker-2 (fencing naik ke 2)
    await service.admit_call(
        user_id=fx["user_id"], session_id=call.session_id,
        lease_owner="worker-2", lease_seconds=3600,
    )
    # worker-1 dengan token lama mencoba menulis turn → ditolak
    with pytest.raises(ConflictError, match="stale"):
        await service.record_turn(
            user_id=fx["user_id"], session_id=call.session_id,
            fencing_token=1, lease_owner="worker-1", speaker="user", epoch=0,
        )
    # Lease owner salah → ditolak
    with pytest.raises(ConflictError, match="stale"):
        await service.record_turn(
            user_id=fx["user_id"], session_id=call.session_id,
            fencing_token=2, lease_owner="worker-1", speaker="user", epoch=0,
        )


@requires_db
async def test_turns_sequence_and_barge_in(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    call = await service.create_call(
        user_id=fx["user_id"], agent_code="elean", mode="video", consent_version="c1"
    )
    await service.admit_call(
        user_id=fx["user_id"], session_id=call.session_id,
        lease_owner="worker-1", lease_seconds=3600,
    )
    await service.activate_call(
        user_id=fx["user_id"], session_id=call.session_id,
        fencing_token=1, lease_owner="worker-1",
    )
    t1 = await service.record_turn(
        user_id=fx["user_id"], session_id=call.session_id,
        fencing_token=1, lease_owner="worker-1", speaker="user", epoch=0,
    )
    t2 = await service.record_turn(
        user_id=fx["user_id"], session_id=call.session_id,
        fencing_token=1, lease_owner="worker-1", speaker="agent", epoch=0,
    )
    assert (t1.sequence, t2.sequence) == (1, 2)

    # Barge-in pada turn agent
    interrupted = await service.interrupt_turn(
        user_id=fx["user_id"], session_id=call.session_id, turn_id=t2.id,
        fencing_token=1, lease_owner="worker-1",
    )
    assert interrupted.state == "interrupted"
    assert interrupted.interrupted_at is not None
    # Turn tidak terhapus
    from temanbule.modules.calls.models import CallTurn

    still_there = (
        await db.execute(select(CallTurn).where(CallTurn.id == t2.id))
    ).scalar_one_or_none()
    assert still_there is not None


@requires_db
async def test_graceful_end_idempotent_low_balance(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    call = await service.create_call(
        user_id=fx["user_id"], agent_code="elean", mode="voice", consent_version="c1"
    )
    await service.admit_call(
        user_id=fx["user_id"], session_id=call.session_id,
        lease_owner="worker-1", lease_seconds=3600,
    )
    ended = await service.end_call(
        user_id=fx["user_id"], session_id=call.session_id, end_reason="low_balance"
    )
    assert ended.state == "ended"
    assert ended.end_reason == "low_balance"
    assert ended.lease_owner is None

    # End ulang idempoten
    again = await service.end_call(
        user_id=fx["user_id"], session_id=call.session_id, end_reason="user_hangup"
    )
    assert again.end_reason == "low_balance"  # tidak berubah

    with pytest.raises(ValidationError, match="end_reason"):
        await service.end_call(
            user_id=fx["user_id"], session_id=call.session_id, end_reason="alien"
        )


@requires_db
async def test_cross_owner_call_denied(db: AsyncSession) -> None:
    fx = await _fixture(db)
    service = CallService(db)
    call = await service.create_call(
        user_id=fx["user_id"], agent_code="elean", mode="voice", consent_version="c1"
    )
    stranger = new_ulid()
    with pytest.raises(NotFoundError):
        await service.get_call(user_id=stranger, session_id=call.session_id)
    with pytest.raises(NotFoundError):
        await service.admit_call(
            user_id=stranger, session_id=call.session_id,
            lease_owner="w", lease_seconds=60,
        )
