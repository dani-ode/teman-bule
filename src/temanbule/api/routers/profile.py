"""Profile router (foundation + Phase 4): profile, facts, assessments."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.conversations.assessments import AssessmentService
from temanbule.modules.conversations.facts import FactsService
from temanbule.modules.identity.repository import IdentityRepository
from temanbule.platform.errors import ConflictError

router = APIRouter(prefix="/v1/me", tags=["profile"])


class ProfileResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    display_name: str | None
    english_level: str | None
    learning_goals: list[str] | None
    status: str


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    english_level: str | None = Field(default=None, max_length=20)
    learning_goals: list[str] | None = Field(default=None, max_length=10)


class FactResponse(BaseModel):
    fact_id: str
    fact_key: str
    value: str
    confidence: float
    status: str
    provenance_ref: str | None
    created_at: str


class AssessmentResponse(BaseModel):
    assessment_id: str
    session_id: str
    rubric_version: str
    dimensions: dict[str, int]
    suggested_level: str | None
    created_at: str


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(current_user: CurrentUser, session: SessionDep) -> ProfileResponse:
    repo = IdentityRepository(session)
    profile = await repo.get_profile(current_user.id)
    learning_goals = None
    if profile and profile.learning_goals:
        try:
            learning_goals = json.loads(profile.learning_goals)
        except (ValueError, TypeError):
            learning_goals = None
    return ProfileResponse(
        user_id=current_user.id,
        email=current_user.normalized_email,
        email_verified=current_user.email_verified_at is not None,
        display_name=profile.display_name if profile else None,
        english_level=profile.english_level if profile else None,
        learning_goals=learning_goals,
        status=current_user.status,
    )


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> ProfileResponse:
    repo = IdentityRepository(session)
    profile = await repo.get_profile(current_user.id)
    if profile is None:
        from temanbule.modules.identity.models import UserProfile
        profile = UserProfile(user_id=current_user.id)
        repo.add_profile(profile)
        await session.flush()
    if body.display_name is not None:
        profile.display_name = body.display_name
    if body.english_level is not None:
        profile.english_level = body.english_level
    if body.learning_goals is not None:
        profile.learning_goals = json.dumps(body.learning_goals)
    await session.commit()
    return await get_profile(current_user, session)


@router.get("/facts", response_model=list[FactResponse])
async def list_facts(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FactResponse]:
    service = FactsService(session)
    facts = await service.list_facts(user_id=current_user.id, limit=limit)
    return [
        FactResponse(
            fact_id=f.id,
            fact_key=f.fact_key,
            value=f.value,
            confidence=f.confidence,
            status=f.status,
            provenance_ref=f.provenance_ref,
            created_at=f.created_at.isoformat(),
        )
        for f in facts
    ]


@router.get("/assessments", response_model=list[AssessmentResponse])
async def list_assessments(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AssessmentResponse]:
    service = AssessmentService(session)
    assessments = await service.list_assessments(user_id=current_user.id, limit=limit)
    return [
        AssessmentResponse(
            assessment_id=a.id,
            session_id=a.session_id,
            rubric_version=a.rubric_version,
            dimensions=json.loads(a.dimensions),
            suggested_level=a.suggested_level,
            created_at=a.created_at.isoformat(),
        )
        for a in assessments
    ]
