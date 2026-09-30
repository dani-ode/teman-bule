"""Learning (Home) router (Phase 4): courses, structure, lessons, progress."""

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


class UnitResponse(BaseModel):
    unit_id: str
    title: str
    position: int
    lessons: list[LessonResponse]


class LessonResponse(BaseModel):
    lesson_id: str
    slug: str
    title: str
    level: str
    position: int
    status: str


class CourseStructureResponse(BaseModel):
    course_id: str
    slug: str
    title: str
    level: str
    units: list[UnitResponse]


@router.get("/courses/{course_id}/structure", response_model=CourseStructureResponse)
async def get_course_structure(course_id: str, session: SessionDep) -> CourseStructureResponse:
    service = LearningService(session)
    course = await service.get_course(course_id)
    units = await service.list_units(course_id)
    unit_responses = []
    for unit in units:
        lessons = await service.list_lessons(unit.id)
        unit_responses.append(
            UnitResponse(
                unit_id=unit.id,
                title=unit.title,
                position=unit.position,
                lessons=[
                    LessonResponse(
                        lesson_id=lesson.id,
                        slug=lesson.slug,
                        title=lesson.title,
                        level=lesson.level,
                        position=lesson.position,
                        status=lesson.status,
                    )
                    for lesson in lessons
                ],
            )
        )
    return CourseStructureResponse(
        course_id=course.id,
        slug=course.slug,
        title=course.title,
        level=course.level,
        units=unit_responses,
    )


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
