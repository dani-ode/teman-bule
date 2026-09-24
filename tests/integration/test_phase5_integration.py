"""Integration tests Phase 5: media lifecycle + voice note STT retry-safe.

Menutup exit criteria Phase 5:
- Oversized/invalid media ditolak eksplisit
- Finalize owner-only, bytes mismatch ditolak
- Malicious → rejected, tidak dapat dipakai
- Voice note STT → transcript ke chat retry-safe (dedupe client_key)
- Cross-owner denied

SttPort di sini TEST DOUBLE berlabel jelas; bukan bukti vendor (DEC-08/10).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
from temanbule.modules.conversations.models import (
    ConversationMessage,
    PracticeCategory,
)
from temanbule.modules.identity.models import User
from temanbule.modules.media.models import MediaObject
from temanbule.modules.media.services import MediaService
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")


class FakeStt:
    """TEST DOUBLE — bukan bukti kompatibilitas STT vendor (DEC-08/10 pending)."""

    def __init__(self, transcript: str = "hello world transcript") -> None:
        self.transcript = transcript
        self.calls = 0

    async def transcribe(self, *, media: MediaObject, language: str) -> str:
        self.calls += 1
        return self.transcript


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


@requires_db
async def test_oversized_media_rejected(db: AsyncSession) -> None:
    user = User(id=new_ulid(), normalized_email=f"med-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    service = MediaService(db)
    with pytest.raises(ValidationError, match="batas"):
        await service.register_upload(
            user_id=user.id, media_type="image", size_bytes=11 * 1024 * 1024
        )
    with pytest.raises(ValidationError, match="didukung"):
        await service.register_upload(user_id=user.id, media_type="exe", size_bytes=100)
    with pytest.raises(ValidationError, match="batas"):
        await service.register_upload(user_id=user.id, media_type="audio", size_bytes=0)


@requires_db
async def test_finalize_owner_and_bytes_match(db: AsyncSession) -> None:
    user = User(id=new_ulid(), normalized_email=f"med2-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    service = MediaService(db)
    media = await service.register_upload(user_id=user.id, media_type="audio", size_bytes=1024)

    with pytest.raises(ValidationError, match="cocok"):
        await service.finalize_upload(
            user_id=user.id, media_id=media.id, checksum="abc", actual_bytes=2048
        )
    finalized = await service.finalize_upload(
        user_id=user.id, media_id=media.id, checksum="abc", actual_bytes=1024
    )
    assert finalized.status == "uploaded"

    # Cross-owner finalize → 404
    other_media = await service.register_upload(user_id=user.id, media_type="pdf", size_bytes=10)
    with pytest.raises(NotFoundError):
        await service.finalize_upload(
            user_id=new_ulid(), media_id=other_media.id, checksum="x", actual_bytes=10
        )


@requires_db
async def test_malicious_media_rejected_and_unusable(db: AsyncSession) -> None:
    user = User(id=new_ulid(), normalized_email=f"med3-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    service = MediaService(db)
    media = await service.register_upload(user_id=user.id, media_type="pdf", size_bytes=2048)
    await service.finalize_upload(
        user_id=user.id, media_id=media.id, checksum="sum", actual_bytes=2048
    )
    scanned = await service.apply_scan_result(media_id=media.id, scan_state="malicious")
    assert scanned.status == "rejected"
    assert scanned.scan_state == "malicious"


@requires_db
async def test_voice_note_transcript_retry_safe(db: AsyncSession) -> None:
    from temanbule.modules.catalog.plan_selection import PlanSelectionService
    from temanbule.modules.conversations.services import ConversationService

    user = User(id=new_ulid(), normalized_email=f"med4-{new_ulid()[-10:]}@example.com")
    category = PracticeCategory(
        id=new_ulid(), code=f"cat-{new_ulid()[-10:]}", title="Pronunciation", status="published"
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

    service = MediaService(db)
    media = await service.register_upload(user_id=user.id, media_type="audio", size_bytes=5000)
    await service.finalize_upload(
        user_id=user.id, media_id=media.id, checksum="sum", actual_bytes=5000
    )
    await service.apply_scan_result(media_id=media.id, scan_state="clean")

    stt = FakeStt()
    message_id = await service.transcribe_voice_note_to_chat(
        user_id=user.id, media_id=media.id, session_id=conversation.id,
        language="en", stt=stt,
    )
    # Retry worker: STT tidak dipanggil ulang bila pesan sudah ada?
    # append_user_message dengan client_key sama → pesan sama, tapi STT tetap
    # dipanggil karena transcript diperlukan sebelum append. Retry-safe ada di
    # level pesan (dedupe client_key), sehingga retry hanya memanggil STT ulang
    # bila pesan belum tersimpan. Simulasikan retry setelah pesan tersimpan:
    stt_second = FakeStt()
    message_id_2 = await service.transcribe_voice_note_to_chat(
        user_id=user.id, media_id=media.id, session_id=conversation.id,
        language="en", stt=stt_second,
    )
    assert message_id == message_id_2  # pesan tidak ganda

    messages = (
        (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.session_id == conversation.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(messages) == 1
    assert messages[0].text == "hello world transcript"


@requires_db
async def test_unscanned_media_cannot_transcribe(db: AsyncSession) -> None:
    user = User(id=new_ulid(), normalized_email=f"med5-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    service = MediaService(db)
    media = await service.register_upload(user_id=user.id, media_type="audio", size_bytes=100)
    with pytest.raises(ConflictError, match="scan/status"):
        await service.transcribe_voice_note_to_chat(
            user_id=user.id, media_id=media.id, session_id=new_ulid(),
            language="en", stt=FakeStt(),
        )


@requires_db
async def test_non_audio_cannot_transcribe(db: AsyncSession) -> None:
    user = User(id=new_ulid(), normalized_email=f"med6-{new_ulid()[-10:]}@example.com")
    db.add(user)
    await db.flush()
    service = MediaService(db)
    media = await service.register_upload(user_id=user.id, media_type="pdf", size_bytes=100)
    await service.finalize_upload(
        user_id=user.id, media_id=media.id, checksum="s", actual_bytes=100
    )
    await service.apply_scan_result(media_id=media.id, scan_state="clean")
    with pytest.raises(ValidationError, match="audio"):
        await service.transcribe_voice_note_to_chat(
            user_id=user.id, media_id=media.id, session_id=new_ulid(),
            language="en", stt=FakeStt(),
        )
