"""Profile router (foundation slice)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from temanbule.api.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/v1/me", tags=["profile"])


class ProfileResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    display_name: str | None
    status: str


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(current_user: CurrentUser, session: SessionDep) -> ProfileResponse:
    from temanbule.modules.identity.repository import IdentityRepository

    repo = IdentityRepository(session)
    profile = await repo.get_profile(current_user.id)
    return ProfileResponse(
        user_id=current_user.id,
        email=current_user.normalized_email,
        email_verified=current_user.email_verified_at is not None,
        display_name=profile.display_name if profile else None,
        status=current_user.status,
    )
