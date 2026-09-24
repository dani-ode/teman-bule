"""Learning (Home) router (Phase 4)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.learning.services import LearningService

router = APIRouter(prefix="/v1", tags=["learning"])


class CourseResponse(BaseModel):
    course_id: str
    slug: str
    title: str
    level: str


class LessonContentResponse(BaseModel):
    content_version_id: str
    lesson_id: str
    revision: int
    content_type: str
    body: str


class ProgressRequest(BaseModel):
    status: str = Field(pattern="^(started|completed)$")
    completion_percent: int = Field(ge=0, le=100)


class ProgressResponse(BaseModel):
    progress_id: str
    content_version_id: str
    status: str
    completion_percent: int


@router.get("/courses", response_model=list[CourseResponse])
async def list_courses(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CourseResponse]:
    service = LearningService(session)
    courses = await service.list_published_courses(limit=limit)
    return [
        CourseResponse(course_id=c.id, slug=c.slug, title=c.title, level=c.level)
        for c in courses
    ]


@router.get("/lessons/{lesson_id}", response_model=LessonContentResponse)
async def get_lesson(lesson_id: str, session: SessionDep) -> LessonContentResponse:
    service = LearningService(session)
    content = await service.get_published_lesson_content(lesson_id)
    return LessonContentResponse(
        content_version_id=content.id,
        lesson_id=content.lesson_id,
        revision=content.revision,
        content_type=content.content_type,
        body=content.body,
    )


@router.put("/learning-progress/{content_version_id}", response_model=ProgressResponse)
async def put_progress(
    content_version_id: str,
    body: ProgressRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> ProgressResponse:
    service = LearningService(session)
    progress = await service.record_progress(
        user_id=current_user.id,
        content_version_id=content_version_id,
        status=body.status,
        completion_percent=body.completion_percent,
    )
    await session.commit()
    return ProgressResponse(
        progress_id=progress.id,
        content_version_id=progress.content_version_id,
        status=progress.status,
        completion_percent=progress.completion_percent,
    )


@router.get("/me/progress", response_model=list[ProgressResponse])
async def get_my_progress(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ProgressResponse]:
    service = LearningService(session)
    rows = await service.get_user_progress(user_id=current_user.id, limit=limit)
    return [
        ProgressResponse(
            progress_id=p.id,
            content_version_id=p.content_version_id,
            status=p.status,
            completion_percent=p.completion_percent,
        )
        for p in rows
    ]
