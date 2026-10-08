"""Integration tests Phase 8: multi-store deletion.

Menutup exit criteria Phase 8 (bagian deletion):
- Deletion request idempoten per user (satu aktif)
- Vector projections tombstone di semua profile spaces sebelum doc deleted
- Private data (vocabulary, facts) terhapus; media/documents tombstone
- Financial records (wallet, payment orders, ledger) TIDAK terhapus
- User dianonimkan (email), profile dikosongkan
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.billing.models import PaymentOrder, TokenPackageVersion, Wallet
from temanbule.modules.billing.wallet import WalletService
from temanbule.modules.conversations.models import UserFact
from temanbule.modules.identity.deletion import DeletionService
from temanbule.modules.identity.models import User, UserProfile
from temanbule.modules.knowledge.models import (
    EmbeddingProjection,
    KnowledgeChunk,
    KnowledgeDocument,
)
from temanbule.modules.knowledge.services import KnowledgeService
from temanbule.modules.media.models import MediaObject
from temanbule.modules.media.services import MediaService
from temanbule.modules.vocabulary.models import VocabularyEntry
from temanbule.modules.vocabulary.services import VocabularyService
from temanbule.platform.security import new_ulid, sha256_hex

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


async def _rich_user_fixture(db: AsyncSession) -> dict[str, str]:
    """User dengan data di semua store: vocab, facts, knowledge+projection,
    media, wallet+order (financial)."""
    user = User(id=new_ulid(), normalized_email=f"del-{new_ulid()[-10:]}@example.com")
    profile = UserProfile(user_id=user.id, display_name="To Delete")
    db.add_all([user, profile])
    await db.flush()

    vocab_service = VocabularyService(db)
    entry, _ = await vocab_service.save_entry(user_id=user.id, lemma="delete-me", language="en")

    facts_service_facts = UserFact(
        id=new_ulid(), user_id=user.id, fact_key="x", value="y",
        confidence=0.5, status="proposed", provenance_ref='["m"]', source_version="v1",
    )
    db.add(facts_service_facts)
    await db.flush()

    knowledge = KnowledgeService(db)
    document, _ = await knowledge.commit_canonical_document(
        scope="user_memory", source_type="conversation_extraction",
        source_id=new_ulid(), source_version="v1",
        content_hash=sha256_hex("content"), chunks=["private chunk"],
        owner_user_id=user.id,
    )
    chunk_id = (
        await db.execute(
            select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document.id)
        )
    ).scalar_one()
    # Dua projection pada dua profile nyata (simulasi dua vector spaces)
    from temanbule.modules.catalog.models import AiModelConfiguration, ProviderCatalog
    from temanbule.modules.knowledge.models import EmbeddingProfile

    for label in ("gemini", "openai"):
        suffix = new_ulid()[-10:]
        provider = ProviderCatalog(id=new_ulid(), code=f"{label}-{suffix}", status="active")
        db.add(provider)
        await db.flush()
        model = AiModelConfiguration(
            id=new_ulid(), provider_id=provider.id, identifier=f"emb-{suffix}",
            revision=1, capabilities='["embedding"]', adapter=label,
            adapter_version="1", status="active",
        )
        db.add(model)
        await db.flush()
        profile = EmbeddingProfile(
            id=new_ulid(), provider_id=provider.id, model_id=model.id,
            model_revision=1, dimension=3072, document_task_type="doc",
            query_task_type="query", normalization="l2", generation=1, status="active",
        )
        db.add(profile)
        await db.flush()
        db.add(
            EmbeddingProjection(
                id=new_ulid(), chunk_id=chunk_id, source_version="v1",
                profile_id=profile.id, generation=1,
                vector_id=f"vec-{label}", state="projected", content_hash="h",
            )
        )
    await db.flush()

    media_service = MediaService(db)
    media = await media_service.register_upload(user_id=user.id, media_type="audio", size_bytes=100)

    wallets = WalletService(db)
    await wallets.credit_topup(user_id=user.id, order_id=new_ulid(), token_units=500)
    package = TokenPackageVersion(
        id=new_ulid(), package_code=f"pack-{new_ulid()[-10:]}", revision=1,
        currency="IDR", amount_minor=10000, token_units=100, status="published",
    )
    db.add(package)
    await db.flush()
    order = PaymentOrder(
        id=new_ulid(), user_id=user.id, package_version_id=package.id,
        merchant_reference=f"ref-{new_ulid()[-10:]}", amount_minor=10000,
        currency="IDR", token_units=100, state="paid",
    )
    db.add(order)
    await db.flush()

    return {
        "user_id": user.id,
        "entry_id": entry.id,
        "document_id": document.id,
        "chunk_id": chunk_id,
        "media_id": media.id,
        "order_id": order.id,
    }


@requires_db
async def test_deletion_request_idempotent(db: AsyncSession) -> None:
    fx = await _rich_user_fixture(db)
    service = DeletionService(db)
    first = await service.request_deletion(user_id=fx["user_id"])
    second = await service.request_deletion(user_id=fx["user_id"])
    assert first.id == second.id


@requires_db
async def test_full_deletion_multi_store(db: AsyncSession) -> None:
    fx = await _rich_user_fixture(db)
    service = DeletionService(db)
    request = await service.request_deletion(user_id=fx["user_id"])
    completed = await service.execute_deletion(request_id=request.id)

    assert completed.status == "completed"
    assert completed.tombstone_version == 2

    # Vector projections tombstone di SEMUA profile spaces
    projections = (
        (
            await db.execute(
                select(EmbeddingProjection).where(
                    EmbeddingProjection.chunk_id == fx["chunk_id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(projections) == 2
    assert all(p.state == "deleted" for p in projections)

    # Knowledge document tombstone
    document = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == fx["document_id"])
        )
    ).scalar_one()
    assert document.deleted_at is not None
    assert document.indexing_state == "deleted"

    # Media deleted
    media = (
        await db.execute(select(MediaObject).where(MediaObject.id == fx["media_id"]))
    ).scalar_one()
    assert media.status == "deleted"

    # Vocabulary hard deleted
    entry = (
        await db.execute(select(VocabularyEntry).where(VocabularyEntry.id == fx["entry_id"]))
    ).scalar_one_or_none()
    assert entry is None

    # Facts hard deleted
    facts = (
        (await db.execute(select(UserFact).where(UserFact.user_id == fx["user_id"])))
        .scalars()
        .all()
    )
    assert facts == []

    # User anonymized + deleted
    user = (await db.execute(select(User).where(User.id == fx["user_id"]))).scalar_one()
    assert user.status == "deleted"
    assert "@deleted.temanbule.local" in user.normalized_email
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == fx["user_id"]))
    ).scalar_one()
    assert profile.display_name is None

    # FINANCIAL UNTOUCHED: wallet + order tetap ada
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx["user_id"]))
    ).scalar_one_or_none()
    assert wallet is not None
    assert wallet.available_units == 500
    order = (
        await db.execute(select(PaymentOrder).where(PaymentOrder.id == fx["order_id"]))
    ).scalar_one_or_none()
    assert order is not None
    assert order.state == "paid"


@requires_db
async def test_deletion_execute_idempotent_replay(db: AsyncSession) -> None:
    fx = await _rich_user_fixture(db)
    service = DeletionService(db)
    request = await service.request_deletion(user_id=fx["user_id"])
    first = await service.execute_deletion(request_id=request.id)
    replay = await service.execute_deletion(request_id=request.id)
    assert replay.id == first.id
    assert replay.status == "completed"
    assert replay.tombstone_version == first.tombstone_version
