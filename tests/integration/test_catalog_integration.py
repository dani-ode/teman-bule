"""Integration tests catalog/BYOK/plan selection pada PostgreSQL nyata.

Menutup exit criteria Phase 2:
- snapshot Advance wajib BYOK LLM+STT aktif (tanpa fallback)
- credential provider mismatch ditolak
- revoke credential menghapus selection
- plan switch diblok 409 bila reservation terbuka
- secret tidak tersimpan plaintext

Verifier di sini TEST DOUBLE berlabel jelas; bukan bukti kompatibilitas
vendor (DEC-08 pending).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.billing.wallet import WalletService
from temanbule.modules.catalog.byok import ByokCredentialService
from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    AiModelConfiguration,
    Plan,
    PlanPolicyVersion,
    ProviderCatalog,
    UserAiSelection,
)
from temanbule.modules.catalog.plan_selection import PlanSelectionService
from temanbule.modules.catalog.services import RuntimeSnapshotBuilder
from temanbule.modules.identity.models import User
from temanbule.platform.crypto import FieldCipher
from temanbule.platform.errors import ConflictError, ForbiddenError, ValidationError
from temanbule.platform.security import new_ulid

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")

TEST_KEK = FieldCipher.generate_kek()


class FakeVerifier:
    """TEST DOUBLE — bukan bukti kompatibilitas provider (DEC-08 pending)."""

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[str] = []

    async def verify(self, *, provider_code: str, api_key: str, base_url: str | None) -> bool:
        self.calls.append(provider_code)
        return self.result


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
    suffix = new_ulid()[:8]
    user = User(id=new_ulid(), normalized_email=f"cat-{suffix}@example.com")
    provider = ProviderCatalog(id=new_ulid(), code=f"prov-{suffix}", status="active")
    model = AiModelConfiguration(
        id=new_ulid(),
        provider_id=provider.id,
        identifier=f"model-{suffix}",
        revision=1,
        capabilities='["llm","stt"]',
        adapter="openai",
        adapter_version="1",
        status="active",
    )
    # Agent/plans adalah global natural keys — reuse bila sudah ada dari test lain.
    agent = (
        await db.execute(select(Agent).where(Agent.code == "elean"))
    ).scalar_one_or_none()
    db.add_all([user, provider, model])
    await db.flush()
    if agent is None:
        agent = Agent(id=new_ulid(), code="elean", display_name="Elean", status="active")
        db.add(agent)
        await db.flush()
    if agent.active_version_id is None:
        agent_version = AgentVersion(
            id=new_ulid(), agent_id=agent.id, revision=1, status="published"
        )
        db.add(agent_version)
        await db.flush()
        agent.active_version_id = agent_version.id

    for plan_code in ("vip", "advance"):
        existing_plan = (
            await db.execute(select(Plan).where(Plan.code == plan_code))
        ).scalar_one_or_none()
        if existing_plan is None:
            plan = Plan(code=plan_code, status="active")
            policy = PlanPolicyVersion(
                id=new_ulid(),
                plan_code=plan_code,
                revision=1,
                policy="{}",
                status="published",
            )
            db.add_all([plan, policy])
            await db.flush()
            plan.policy_version = 1
            await db.flush()
    return {
        "user_id": user.id,
        "provider_id": provider.id,
        "model_id": model.id,
        "agent_code": agent.code,
    }


def _byok(db: AsyncSession, verify: bool = True) -> ByokCredentialService:
    return ByokCredentialService(db, FieldCipher(TEST_KEK), FakeVerifier(verify))


@requires_db
async def test_register_credential_encrypted_no_plaintext(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    svc = _byok(db)
    secret = "sk-test-super-secret-key-1234567890"  # noqa: S105 - fixture test
    credential = await svc.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key=secret
    )
    assert credential.status == "active"
    assert credential.verified_at is not None
    assert secret not in credential.encrypted_api_key
    assert credential.fingerprint != secret
    # Dekripsi round-trip berfungsi
    decrypted = await svc.get_decrypted_key(user_id=fx["user_id"], credential_id=credential.id)
    assert decrypted == secret


@requires_db
async def test_failed_verification_raises(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    svc = _byok(db, verify=False)
    with pytest.raises(ConflictError, match="verifikasi"):
        await svc.register_credential(
            user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="bad-key"  # noqa: S106
        )


@requires_db
async def test_custom_base_url_rejected_when_provider_disallows(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    svc = _byok(db)
    with pytest.raises(ValidationError, match="base_url"):
        await svc.register_credential(
            user_id=fx["user_id"],
            provider_id=fx["provider_id"],
            api_key="sk-test-1234567890123456",  # noqa: S106
            base_url="https://evil.example.com",
        )


@requires_db
async def test_selection_provider_mismatch_rejected(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    suffix = new_ulid()[:8]
    other_provider = ProviderCatalog(id=new_ulid(), code=f"other-{suffix}", status="active")
    other_model = AiModelConfiguration(
        id=new_ulid(),
        provider_id=other_provider.id,
        identifier=f"m-{suffix}",
        revision=1,
        capabilities='["llm"]',
        adapter="gemini",
        adapter_version="1",
        status="active",
    )
    db.add_all([other_provider, other_model])
    await db.flush()

    svc = _byok(db)
    credential = await svc.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="sk-test-aaaaaaa"  # noqa: S106
    )
    with pytest.raises(ConflictError, match="provider"):
        await svc.select_model(
            user_id=fx["user_id"],
            capability="llm",
            credential_id=credential.id,
            model_id=other_model.id,
        )


@requires_db
async def test_cross_owner_credential_access_denied(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    svc = _byok(db)
    credential = await svc.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="sk-test-bbbbbb"  # noqa: S106
    )
    other_user_id = new_ulid()
    with pytest.raises(ForbiddenError):
        await svc.get_decrypted_key(user_id=other_user_id, credential_id=credential.id)


@requires_db
async def test_revoke_credential_removes_selection(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    svc = _byok(db)
    credential = await svc.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="sk-test-cccccc"  # noqa: S106
    )
    await svc.select_model(
        user_id=fx["user_id"],
        capability="llm",
        credential_id=credential.id,
        model_id=fx["model_id"],
    )
    await svc.revoke_credential(user_id=fx["user_id"], credential_id=credential.id)

    selection = (
        await db.execute(
            select(UserAiSelection).where(
                UserAiSelection.user_id == fx["user_id"],
                UserAiSelection.capability == "llm",
            )
        )
    ).scalar_one_or_none()
    assert selection is None
    await db.refresh(credential)
    assert credential.status == "revoked"
    assert credential.revoked_at is not None


@requires_db
async def test_advance_snapshot_requires_full_byok(db: AsyncSession) -> None:
    """Switch ke Advance tanpa selection STT → 409; user tetap VIP dan
    snapshot VIP tetap bisa dibangun (tanpa BYOK)."""
    fx = await _base_fixture(db)
    byok = _byok(db)
    credential = await byok.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="sk-test-dddddd"  # noqa: S106
    )
    await byok.select_model(
        user_id=fx["user_id"],
        capability="llm",
        credential_id=credential.id,
        model_id=fx["model_id"],
    )
    plans = PlanSelectionService(db)
    await plans.select_plan(user_id=fx["user_id"], plan_code="vip")
    # Pindah ke advance tanpa STT selection → 409
    with pytest.raises(ConflictError, match="LLM dan STT"):
        await plans.select_plan(user_id=fx["user_id"], plan_code="advance")

    # Tetap VIP: snapshot platform-key berhasil tanpa model BYOK.
    builder = RuntimeSnapshotBuilder(db)
    snapshot = await builder.build_for_user(
        user_id=fx["user_id"], agent_code=fx["agent_code"]
    )
    assert snapshot.principal_kind == "user"
    assert snapshot.llm_model_configuration_id is None
    assert snapshot.stt_model_configuration_id is None


@requires_db
async def test_advance_full_flow_snapshot_built(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    byok = _byok(db)
    credential = await byok.register_credential(
        user_id=fx["user_id"], provider_id=fx["provider_id"], api_key="sk-test-eeeeee"  # noqa: S106
    )
    for capability in ("llm", "stt"):
        await byok.select_model(
            user_id=fx["user_id"],
            capability=capability,
            credential_id=credential.id,
            model_id=fx["model_id"],
        )
    plans = PlanSelectionService(db)
    await plans.select_plan(user_id=fx["user_id"], plan_code="vip")
    selection = await plans.select_plan(user_id=fx["user_id"], plan_code="advance")
    assert selection.plan_code == "advance"

    builder = RuntimeSnapshotBuilder(db)
    snapshot = await builder.build_for_user(
        user_id=fx["user_id"], agent_code=fx["agent_code"]
    )
    assert snapshot.principal_kind == "user"
    assert snapshot.owner_user_id == fx["user_id"]
    assert snapshot.llm_model_configuration_id == fx["model_id"]
    assert snapshot.stt_model_configuration_id == fx["model_id"]
    assert snapshot.agent_version_id is not None


@requires_db
async def test_plan_switch_blocked_by_open_reservation(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    from temanbule.modules.billing.models import RateCardVersion

    card = RateCardVersion(
        id=new_ulid(),
        version=f"v-{new_ulid()[:8]}",
        effective_at=datetime.now(UTC),
        rounding_policy="half_up",
        status="published",
    )
    db.add(card)
    await db.flush()

    plans = PlanSelectionService(db)
    await plans.select_plan(user_id=fx["user_id"], plan_code="vip")

    wallets = WalletService(db)
    await wallets.credit_topup(user_id=fx["user_id"], order_id=new_ulid(), token_units=100)
    await wallets.authorize_reservation(
        user_id=fx["user_id"],
        operation_id=f"op-{new_ulid()}",
        rate_card_id=card.id,
        units=50,
        lease_seconds=3600,
    )
    with pytest.raises(ConflictError, match="reservation"):
        await plans.select_plan(user_id=fx["user_id"], plan_code="advance")


@requires_db
async def test_plan_optimistic_concurrency_conflict(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    plans = PlanSelectionService(db)
    selected = await plans.select_plan(user_id=fx["user_id"], plan_code="vip")
    with pytest.raises(ConflictError, match="muat ulang"):
        await plans.select_plan(
            user_id=fx["user_id"], plan_code="advance", expected_revision=999
        )
    assert selected.plan_code == "vip"


@requires_db
async def test_plan_change_events_append_only(db: AsyncSession) -> None:
    fx = await _base_fixture(db)
    plans = PlanSelectionService(db)
    await plans.select_plan(user_id=fx["user_id"], plan_code="vip")

    from temanbule.modules.catalog.models import PlanChangeEvent

    events = (
        (
            await db.execute(
                select(PlanChangeEvent).where(PlanChangeEvent.user_id == fx["user_id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].old_plan_code is None
    assert events[0].new_plan_code == "vip"
