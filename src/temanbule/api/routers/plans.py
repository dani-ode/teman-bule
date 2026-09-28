"""Plans router: selection & switching (Phase 2) + agents catalog."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.catalog.models import Agent, UserPlanSelection
from temanbule.modules.catalog.plan_selection import PlanSelectionService

router = APIRouter(prefix="/v1/me", tags=["plans"])


class PlanSelectionRequest(BaseModel):
    plan_code: str = Field(pattern="^(vip|advance)$")
    expected_revision: int | None = None


class PlanSelectionResponse(BaseModel):
    plan_code: str
    revision: int
    selected_at: str


@router.put("/plan", response_model=PlanSelectionResponse)
async def select_plan(
    body: PlanSelectionRequest, current_user: CurrentUser, session: SessionDep
) -> PlanSelectionResponse:
    service = PlanSelectionService(session)
    selection = await service.select_plan(
        user_id=current_user.id,
        plan_code=body.plan_code,
        expected_revision=body.expected_revision,
    )
    await session.commit()
    return PlanSelectionResponse(
        plan_code=selection.plan_code or "",
        revision=selection.revision or 0,
        selected_at=selection.selected_at.isoformat() if selection.selected_at else "",
    )


@router.get("/plan", response_model=PlanSelectionResponse)
async def get_plan(current_user: CurrentUser, session: SessionDep) -> PlanSelectionResponse:
    from sqlalchemy import select

    from temanbule.platform.errors import NotFoundError

    selection = (
        await session.execute(
            select(UserPlanSelection).where(UserPlanSelection.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if selection is None or selection.plan_code is None:
        raise NotFoundError("Plan belum dipilih.")
    return PlanSelectionResponse(
        plan_code=selection.plan_code,
        revision=selection.revision or 0,
        selected_at=selection.selected_at.isoformat() if selection.selected_at else "",
    )


class AgentResponse(BaseModel):
    agent_id: str
    code: str
    display_name: str
    active_version_id: str | None


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AgentResponse]:
    from sqlalchemy import select
    rows = (
        (
            await session.execute(
                select(Agent)
                .where(Agent.status == "active")
                .order_by(Agent.display_name.asc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        AgentResponse(
            agent_id=a.id,
            code=a.code,
            display_name=a.display_name,
            active_version_id=a.active_version_id,
        )
        for a in rows
    ]
