"""TOEFL service (Phase 4): attempts, submissions, deterministic scoring.

Kontrak (implementation-plan.md, callcraft-tools.md, postgresql-schema.md):
- State machine: created → in_progress → submitted → evaluating →
  evaluated|evaluation_failed.
- Submissions locked setelah submit.
- Objective scoring DETERMINISTIK di backend, tidak dapat dioverride
  evaluator AI. Subjective flow (Langflow) menunggu DEC-10/13.
- Scores immutable (append-only trigger) + bounded total 0..120.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.assessments.models import (
    ToeflAttempt,
    ToeflScore,
    ToeflSubmission,
    ToeflTestVersion,
)
from temanbule.modules.catalog.services import RuntimeSnapshotBuilder
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

STATE_CREATED = "created"
STATE_IN_PROGRESS = "in_progress"
STATE_SUBMITTED = "submitted"
STATE_EVALUATING = "evaluating"
STATE_EVALUATED = "evaluated"
STATE_FAILED = "evaluation_failed"

MAX_TOTAL_SCORE = 120
OBJECTIVE_SECTIONS = ("reading", "listening")


class ToeflService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.snapshots = RuntimeSnapshotBuilder(session)

    async def start_attempt(
        self, *, user_id: str, test_version_id: str, agent_code: str = "elean"
    ) -> ToeflAttempt:
        test = (
            await self.session.execute(
                select(ToeflTestVersion).where(
                    ToeflTestVersion.id == test_version_id,
                    ToeflTestVersion.publication_state == "published",
                )
            )
        ).scalar_one_or_none()
        if test is None:
            raise NotFoundError("Test version tidak ditemukan atau belum published.")
        snapshot = await self.snapshots.build_for_user(user_id=user_id, agent_code=agent_code)
        attempt = ToeflAttempt(
            id=new_ulid(),
            user_id=user_id,
            test_version_id=test.id,
            runtime_snapshot_id=snapshot.id,
            state=STATE_IN_PROGRESS,
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def put_submission(
        self,
        *,
        user_id: str,
        attempt_id: str,
        question_ref: str,
        section: str,
        answer: str,
    ) -> ToeflSubmission:
        """Upsert jawaban selama attempt masih in_progress. Locked setelah submit."""
        attempt = await self._owned_attempt(user_id, attempt_id, for_update=True)
        if attempt.state != STATE_IN_PROGRESS:
            raise ConflictError(
                "Attempt sudah dikumpulkan; jawaban terkunci.",
                code="ATTEMPT_LOCKED",
            )
        if section not in ("reading", "listening", "speaking", "writing"):
            raise ValidationError(
                "Section tidak valid.",
                details=[{"field": "section", "message": section}],
            )
        existing = (
            await self.session.execute(
                select(ToeflSubmission).where(
                    ToeflSubmission.attempt_id == attempt_id,
                    ToeflSubmission.question_ref == question_ref,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.answer = answer
            await self.session.flush()
            return existing
        submission = ToeflSubmission(
            id=new_ulid(),
            attempt_id=attempt_id,
            question_ref=question_ref,
            section=section,
            answer=answer,
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    async def submit_attempt(self, *, user_id: str, attempt_id: str) -> ToeflAttempt:
        attempt = await self._owned_attempt(user_id, attempt_id, for_update=True)
        if attempt.state == STATE_SUBMITTED:
            return attempt
        if attempt.state != STATE_IN_PROGRESS:
            raise ConflictError(
                "Attempt tidak dapat disubmit dari state saat ini.",
                code="ATTEMPT_STATE_INVALID",
            )
        attempt.state = STATE_SUBMITTED
        attempt.submitted_at = datetime.now(UTC)
        await self.session.flush()
        return attempt

    async def evaluate_objective(
        self, *, attempt_id: str, answer_key: dict[str, str], rubric_version: str
    ) -> ToeflScore:
        """Scoring deterministik objective sections (reading/listening).

        Satu score per (attempt, rubric) — dedupe via UNIQUE; replay aman.
        Total bounded 0..120. Subjective sections dievaluasi flow terpisah.
        """
        attempt = (
            await self.session.execute(select(ToeflAttempt).where(ToeflAttempt.id == attempt_id))
        ).scalar_one_or_none()
        if attempt is None:
            raise NotFoundError("Attempt tidak ditemukan.")
        if attempt.state not in (STATE_SUBMITTED, STATE_EVALUATING, STATE_EVALUATED):
            raise ConflictError(
                "Attempt belum disubmit.",
                code="ATTEMPT_NOT_SUBMITTED",
            )

        existing = (
            await self.session.execute(
                select(ToeflScore).where(
                    ToeflScore.attempt_id == attempt_id,
                    ToeflScore.rubric_version == rubric_version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        submissions = (
            (
                await self.session.execute(
                    select(ToeflSubmission).where(ToeflSubmission.attempt_id == attempt_id)
                )
            )
            .scalars()
            .all()
        )
        dimensions: dict[str, Any] = {}
        correct = 0
        total_objective = 0
        for submission in submissions:
            if submission.section not in OBJECTIVE_SECTIONS:
                continue
            total_objective += 1
            expected = answer_key.get(submission.question_ref)
            is_correct = (
                expected is not None and (submission.answer or "").strip() == expected.strip()
            )
            dimensions[submission.question_ref] = 1 if is_correct else 0
            if is_correct:
                correct += 1

        # Skala linier deterministik ke 0..120 dari objective saja (baseline);
        # rubric final menggabungkan subjective pada fase evaluasi lengkap.
        total = 0 if total_objective == 0 else round(correct / total_objective * MAX_TOTAL_SCORE)
        total = max(0, min(MAX_TOTAL_SCORE, total))

        score = ToeflScore(
            id=new_ulid(),
            attempt_id=attempt_id,
            rubric_version=rubric_version,
            objective_dimensions=json.dumps(dimensions, sort_keys=True),
            total_score=total,
            flow_version="objective-deterministic.v1",
        )
        self.session.add(score)
        if attempt.state in (STATE_SUBMITTED, STATE_EVALUATING):
            attempt.state = STATE_EVALUATED
            attempt.evaluated_at = datetime.now(UTC)
        await self.session.flush()
        return score

    async def get_attempt(self, *, user_id: str, attempt_id: str) -> ToeflAttempt:
        return await self._owned_attempt(user_id, attempt_id)

    async def get_score(self, *, user_id: str, attempt_id: str) -> ToeflScore:
        await self._owned_attempt(user_id, attempt_id)
        score = (
            await self.session.execute(
                select(ToeflScore).where(ToeflScore.attempt_id == attempt_id)
            )
        ).scalar_one_or_none()
        if score is None:
            raise NotFoundError("Score belum tersedia.")
        return score

    async def _owned_attempt(
        self, user_id: str, attempt_id: str, *, for_update: bool = False
    ) -> ToeflAttempt:
        stmt = select(ToeflAttempt).where(ToeflAttempt.id == attempt_id)
        if for_update:
            stmt = stmt.with_for_update()
        attempt = (await self.session.execute(stmt)).scalar_one_or_none()
        if attempt is None or attempt.user_id != user_id:
            raise NotFoundError("Attempt tidak ditemukan.")
        return attempt
