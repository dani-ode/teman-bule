"""Wiring handler tool facts/assessment ke ToolExecutionService (Phase 3)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.conversations.assessments import AssessmentService
from temanbule.modules.conversations.facts import FactsService
from temanbule.platform.errors import ValidationError


async def user_facts_upsert_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    fact_key = arguments.get("fact_key")
    value = arguments.get("value")
    confidence = arguments.get("confidence")
    source_message_ids = arguments.get("source_message_ids")
    proposed_status = arguments.get("proposed_status", "proposed")

    if not isinstance(fact_key, str) or not isinstance(value, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "fact_key/value", "message": "wajib string"}],
        )
    if not isinstance(confidence, int | float):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "confidence", "message": "wajib numerik"}],
        )
    if not isinstance(source_message_ids, list) or not source_message_ids:
        raise ValidationError(
            "source_message_ids wajib non-empty list.",
            details=[{"field": "source_message_ids", "message": "provenance wajib"}],
        )
    if not isinstance(proposed_status, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "proposed_status", "message": "wajib string"}],
        )

    service = FactsService(session)
    fact, created = await service.upsert_fact(
        user_id=owner_user_id,
        fact_key=fact_key,
        value=value,
        confidence=float(confidence),
        provenance_ref=json.dumps(source_message_ids),
        source_version=str(arguments.get("source_version", "extraction.v1")),
        proposed_status=proposed_status,
    )
    return {
        "resource_id": fact.id,
        "resource_version": 1,
        "created": created,
        "status": fact.status,
    }


async def learning_record_assessment_handler(
    session: AsyncSession, owner_user_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    session_id = arguments.get("session_id")
    source_range = arguments.get("source_range")
    rubric_version = arguments.get("rubric_version")
    dimensions = arguments.get("dimensions")

    if not isinstance(session_id, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "session_id", "message": "wajib string"}],
        )
    if (
        not isinstance(source_range, list)
        or len(source_range) != 2
        or not all(isinstance(x, int) for x in source_range)
    ):
        raise ValidationError(
            "source_range wajib [start, end] integer.",
            details=[{"field": "source_range", "message": "format tidak valid"}],
        )
    if not isinstance(rubric_version, str):
        raise ValidationError(
            "Argument tidak valid.",
            details=[{"field": "rubric_version", "message": "wajib string"}],
        )
    if not isinstance(dimensions, dict):
        raise ValidationError(
            "dimensions wajib object.",
            details=[{"field": "dimensions", "message": "wajib object"}],
        )

    service = AssessmentService(session)
    assessment, created = await service.record_assessment(
        user_id=owner_user_id,
        session_id=session_id,
        evidence_start=source_range[0],
        evidence_end=source_range[1],
        rubric_version=rubric_version,
        dimensions=dimensions,
        suggested_level=(
            str(arguments["suggested_level"]) if arguments.get("suggested_level") else None
        ),
        flow_version=str(arguments.get("flow_version", "assessment.v1")),
    )
    return {
        "resource_id": assessment.id,
        "resource_version": 1,
        "created": created,
    }


def register_analysis_tools(service: ToolExecutionService) -> None:
    service.register_handler("user_facts.upsert", user_facts_upsert_handler)
    service.register_handler("learning.record_assessment", learning_record_assessment_handler)
