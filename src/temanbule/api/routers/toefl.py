"""TOEFL router (Phase 4): tests, attempts, submissions, scores."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.assessments.models import ToeflAttempt, ToeflTestVersion
from temanbule.modules.assessments.services import ToeflService

router = APIRouter(prefix="/v1/toefl", tags=["toefl"])


class StartAttemptRequest(BaseModel):
    test_version_id: str = Field(min_length=1, max_length=26)
    agent_code: str = Field(default="elean", pattern="^(elean|willy)$")


class AttemptResponse(BaseModel):
    attempt_id: str
    test_version_id: str
    state: str
    submitted_at: str | None
    evaluated_at: str | None


class SubmissionRequest(BaseModel):
    question_ref: str = Field(min_length=1, max_length=80)
    section: str = Field(pattern="^(reading|listening|speaking|writing)$")
    answer: str = Field(max_length=10000)


class SubmissionResponse(BaseModel):
    submission_id: str
    question_ref: str
    section: str


class ScoreResponse(BaseModel):
    score_id: str
    attempt_id: str
    rubric_version: str
    total_score: int
    review_status: str


class TestResponse(BaseModel):
    test_version_id: str
    code: str
    revision: int
    rubric_version: str
    publication_state: str


def _attempt_response(attempt: ToeflAttempt) -> AttemptResponse:
    return AttemptResponse(
        attempt_id=attempt.id,
        test_version_id=attempt.test_version_id,
        state=attempt.state,
        submitted_at=attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        evaluated_at=attempt.evaluated_at.isoformat() if attempt.evaluated_at else None,
    )


@router.get("/tests", response_model=list[TestResponse])
async def list_tests(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TestResponse]:
    from sqlalchemy import select

    rows = (
        (
            await session.execute(
                select(ToeflTestVersion)
                .where(ToeflTestVersion.publication_state == "published")
                .order_by(ToeflTestVersion.code.asc(), ToeflTestVersion.revision.desc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        TestResponse(
            test_version_id=t.id,
            code=t.code,
            revision=t.revision,
            rubric_version=t.rubric_version,
            publication_state=t.publication_state,
        )
        for t in rows
    ]


@router.post("/attempts", response_model=AttemptResponse, status_code=201)
async def start_attempt(
    body: StartAttemptRequest, current_user: CurrentUser, session: SessionDep
) -> AttemptResponse:
    service = ToeflService(session)
    attempt = await service.start_attempt(
        user_id=current_user.id,
        test_version_id=body.test_version_id,
        agent_code=body.agent_code,
    )
    await session.commit()
    return _attempt_response(attempt)


@router.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(
    attempt_id: str, current_user: CurrentUser, session: SessionDep
) -> AttemptResponse:
    service = ToeflService(session)
    attempt = await service.get_attempt(user_id=current_user.id, attempt_id=attempt_id)
    return _attempt_response(attempt)


@router.put(
    "/attempts/{attempt_id}/submissions/{question_ref}",
    response_model=SubmissionResponse,
)
async def put_submission(
    attempt_id: str,
    question_ref: str,
    body: SubmissionRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> SubmissionResponse:
    service = ToeflService(session)
    submission = await service.put_submission(
        user_id=current_user.id,
        attempt_id=attempt_id,
        question_ref=question_ref,
        section=body.section,
        answer=body.answer,
    )
    await session.commit()
    return SubmissionResponse(
        submission_id=submission.id,
        question_ref=submission.question_ref,
        section=submission.section,
    )


@router.post("/attempts/{attempt_id}:submit", response_model=AttemptResponse)
async def submit_attempt(
    attempt_id: str, current_user: CurrentUser, session: SessionDep
) -> AttemptResponse:
    service = ToeflService(session)
    attempt = await service.submit_attempt(user_id=current_user.id, attempt_id=attempt_id)
    await session.commit()
    return _attempt_response(attempt)


@router.get("/attempts/{attempt_id}/score", response_model=ScoreResponse)
async def get_score(
    attempt_id: str, current_user: CurrentUser, session: SessionDep
) -> ScoreResponse:
    service = ToeflService(session)
    score = await service.get_score(user_id=current_user.id, attempt_id=attempt_id)
    return ScoreResponse(
        score_id=score.id,
        attempt_id=score.attempt_id,
        rubric_version=score.rubric_version,
        total_score=score.total_score,
        review_status=score.review_status,
    )
