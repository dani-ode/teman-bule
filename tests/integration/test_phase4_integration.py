"""Integration tests Phase 4: learning progress + TOEFL deterministic scoring.

Menutup exit criteria Phase 4:
- Progress hanya untuk published content; bounded; tidak mundur diam-diam;
  owner-scoped
- TOEFL: state machine; submissions locked setelah submit; objective scoring
  deterministik + bounded 0..120; scores immutable (trigger); satu score per
  rubric (replay aman); cross-owner denied
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.assessments.models import ToeflTestVersion
from temanbule.modules.assessments.services import ToeflService
from temanbule.modules.catalog.models import Agent, AgentVersion, Plan, PlanPolicyVersion
from temanbule.modules.identity.models import User
from temanbule.modules.learning.models import (
    Course,
    CourseUnit,
    LearningContentVersion,
    Lesson,
)
from temanbule.modules.learning.services import LearningService
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


async def _learning_fixture(db: AsyncSession) -> dict[str, str]:
    user = User(id=new_ulid(), normalized_email=f"learn-{new_ulid()[-10:]}@example.com")
    course = Course(
        id=new_ulid(),
        slug=f"course-{new_ulid()[-10:]}",
        title="Basics",
        level="A1",
        status="published",
    )
    db.add_all([user, course])
    await db.flush()
    unit = CourseUnit(id=new_ulid(), course_id=course.id, title="Unit 1", position=1)
    db.add(unit)
    await db.flush()
    lesson = Lesson(
        id=new_ulid(), unit_id=unit.id, slug="l1", title="Lesson 1", level="A1",
        position=1, status="published",
    )
    db.add(lesson)
    await db.flush()
    content = LearningContentVersion(
        id=new_ulid(), lesson_id=lesson.id, revision=1, content_type="article",
        body="Content body", publication_state="published",
    )
    db.add(content)
    await db.flush()
    return {"user_id": user.id, "lesson_id": lesson.id, "content_id": content.id}


@requires_db
async def test_progress_requires_published_content(db: AsyncSession) -> None:
    fx = await _learning_fixture(db)
    service = LearningService(db)
    draft = LearningContentVersion(
        id=new_ulid(), lesson_id=fx["lesson_id"], revision=99, content_type="article",
        body="draft", publication_state="draft",
    )
    db.add(draft)
    await db.flush()
    with pytest.raises(NotFoundError):
        await service.record_progress(
            user_id=fx["user_id"], content_version_id=draft.id,
            status="started", completion_percent=10,
        )


@requires_db
async def test_progress_bounded_and_no_regression(db: AsyncSession) -> None:
    fx = await _learning_fixture(db)
    service = LearningService(db)
    with pytest.raises(ValidationError, match="batas"):
        await service.record_progress(
            user_id=fx["user_id"], content_version_id=fx["content_id"],
            status="started", completion_percent=101,
        )
    await service.record_progress(
        user_id=fx["user_id"], content_version_id=fx["content_id"],
        status="started", completion_percent=50,
    )
    with pytest.raises(ConflictError, match="mundur"):
        await service.record_progress(
            user_id=fx["user_id"], content_version_id=fx["content_id"],
            status="started", completion_percent=30,
        )
    done = await service.record_progress(
        user_id=fx["user_id"], content_version_id=fx["content_id"],
        status="completed", completion_percent=100,
    )
    assert done.completion_percent == 100


# --- TOEFL tests ---


async def _toefl_fixture(db: AsyncSession) -> dict[str, str]:
    user = User(id=new_ulid(), normalized_email=f"toefl-{new_ulid()[-10:]}@example.com")
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

    test = ToeflTestVersion(
        id=new_ulid(), code=f"toefl-{new_ulid()[-10:]}", revision=1,
        rubric_version="rubric.v1", definition="{}", publication_state="published",
    )
    db.add(test)
    await db.flush()
    return {"user_id": user.id, "test_version_id": test.id}


@requires_db
async def test_attempt_flow_and_locked_submissions(db: AsyncSession) -> None:
    fx = await _toefl_fixture(db)
    service = ToeflService(db)
    attempt = await service.start_attempt(
        user_id=fx["user_id"], test_version_id=fx["test_version_id"]
    )
    assert attempt.state == "in_progress"

    await service.put_submission(
        user_id=fx["user_id"], attempt_id=attempt.id,
        question_ref="r1", section="reading", answer="A",
    )
    await service.submit_attempt(user_id=fx["user_id"], attempt_id=attempt.id)

    with pytest.raises(ConflictError, match="terkunci"):
        await service.put_submission(
            user_id=fx["user_id"], attempt_id=attempt.id,
            question_ref="r1", section="reading", answer="B",
        )


@requires_db
async def test_objective_scoring_deterministic_bounded(db: AsyncSession) -> None:
    fx = await _toefl_fixture(db)
    service = ToeflService(db)
    attempt = await service.start_attempt(
        user_id=fx["user_id"], test_version_id=fx["test_version_id"]
    )
    for ref, answer in (("r1", "A"), ("r2", "WRONG"), ("l1", "C"), ("l2", "D")):
        section = "reading" if ref.startswith("r") else "listening"
        await service.put_submission(
            user_id=fx["user_id"], attempt_id=attempt.id,
            question_ref=ref, section=section, answer=answer,
        )
    await service.submit_attempt(user_id=fx["user_id"], attempt_id=attempt.id)

    key = {"r1": "A", "r2": "B", "l1": "C", "l2": "D"}
    score = await service.evaluate_objective(
        attempt_id=attempt.id, answer_key=key, rubric_version="rubric.v1"
    )
    # 3 dari 4 benar → 90
    assert score.total_score == 90
    assert 0 <= score.total_score <= 120

    # Replay: satu score per rubric
    again = await service.evaluate_objective(
        attempt_id=attempt.id, answer_key=key, rubric_version="rubric.v1"
    )
    assert again.id == score.id

    await db.refresh(attempt)
    assert attempt.state == "evaluated"


@requires_db
async def test_scores_append_only_enforced(db: AsyncSession) -> None:
    fx = await _toefl_fixture(db)
    service = ToeflService(db)
    attempt = await service.start_attempt(
        user_id=fx["user_id"], test_version_id=fx["test_version_id"]
    )
    await service.put_submission(
        user_id=fx["user_id"], attempt_id=attempt.id,
        question_ref="r1", section="reading", answer="A",
    )
    await service.submit_attempt(user_id=fx["user_id"], attempt_id=attempt.id)
    score = await service.evaluate_objective(
        attempt_id=attempt.id, answer_key={"r1": "A"}, rubric_version="rubric.v1"
    )
    score.total_score = 0  # tamper attempt
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append_only_table"):
        await db.flush()
    await db.rollback()


@requires_db
async def test_evaluate_requires_submitted_attempt(db: AsyncSession) -> None:
    fx = await _toefl_fixture(db)
    service = ToeflService(db)
    attempt = await service.start_attempt(
        user_id=fx["user_id"], test_version_id=fx["test_version_id"]
    )
    with pytest.raises(ConflictError, match="belum disubmit"):
        await service.evaluate_objective(
            attempt_id=attempt.id, answer_key={}, rubric_version="rubric.v1"
        )


@requires_db
async def test_cross_owner_attempt_denied(db: AsyncSession) -> None:
    fx = await _toefl_fixture(db)
    service = ToeflService(db)
    attempt = await service.start_attempt(
        user_id=fx["user_id"], test_version_id=fx["test_version_id"]
    )
    stranger = new_ulid()
    with pytest.raises(NotFoundError):
        await service.get_attempt(user_id=stranger, attempt_id=attempt.id)
    with pytest.raises(NotFoundError):
        await service.submit_attempt(user_id=stranger, attempt_id=attempt.id)
