"""Assessment service (Phase 3): learning assessment records.

Kontrak (callcraft-tools.md, langflow-flows.md):
- record_assessment menyimpan rubric_version + evidence range; TIDAK
  langsung mengganti level profil tanpa policy/konfirmasi.
- Dedupe per (session, evidence_range, rubric, flow) — UNIQUE DB.
- Dimensions terstruktur dan bounded (skor 0..100 per dimensi).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.models import ConversationSession, LearningAssessment
from temanbule.platform.errors import NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

MAX_DIMENSION_SCORE = 100


class AssessmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_assessment(
        self,
        *,
        user_id: str,
        session_id: str,
        evidence_start: int,
        evidence_end: int,
        rubric_version: str,
        dimensions: dict[str, int],
        suggested_level: str | None,
        flow_version: str,
    ) -> tuple[LearningAssessment, bool]:
        """Simpan assessment (idempoten per evidence/rubric/flow)."""
        if evidence_end < evidence_start:
            raise ValidationError("Evidence range tidak valid.")
        if not rubric_version.strip() or not flow_version.strip():
            raise ValidationError("rubric_version dan flow_version wajib.")
        if not dimensions:
            raise ValidationError("dimensions kosong.")
        for name, score in dimensions.items():
            if not isinstance(score, int) or not 0 <= score <= MAX_DIMENSION_SCORE:
                raise ValidationError(
                    "Skor dimensi di luar batas.",
                    details=[{"field": name, "message": f"harus 0..{MAX_DIMENSION_SCORE}"}],
                )

        conversation = (
            await self.session.execute(
                select(ConversationSession).where(ConversationSession.id == session_id)
            )
        ).scalar_one_or_none()
        if conversation is None or conversation.user_id != user_id:
            raise NotFoundError("Session tidak ditemukan.")

        existing = (
            await self.session.execute(
                select(LearningAssessment).where(
                    LearningAssessment.session_id == session_id,
                    LearningAssessment.evidence_start == evidence_start,
                    LearningAssessment.evidence_end == evidence_end,
                    LearningAssessment.rubric_version == rubric_version,
                    LearningAssessment.flow_version == flow_version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        import json

        assessment = LearningAssessment(
            id=new_ulid(),
            user_id=user_id,
            session_id=session_id,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            rubric_version=rubric_version,
            dimensions=json.dumps(dimensions, sort_keys=True),
            suggested_level=suggested_level,
            flow_version=flow_version,
        )
        self.session.add(assessment)
        await self.session.flush()
        return assessment, True

    async def list_assessments(
        self, *, user_id: str, limit: int = 20
    ) -> list[LearningAssessment]:
        rows = (
            (
                await self.session.execute(
                    select(LearningAssessment)
                    .where(LearningAssessment.user_id == user_id)
                    .order_by(LearningAssessment.created_at.desc())
                    .limit(min(limit, 100))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
