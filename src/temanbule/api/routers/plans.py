"""Plans router: selection & switching (Phase 2)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
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

    from temanbule.modules.catalog.models import UserPlanSelection
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
