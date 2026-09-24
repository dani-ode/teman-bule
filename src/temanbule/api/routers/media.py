"""Media router (Phase 5): uploads, finalize, download-url.

Download/upload URL bertipe signed scoped URL — storage adapter konkret
menunggu DEC-15; endpoint gagal eksplisit bila belum terpasang.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.media.services import MediaService
from temanbule.platform.errors import FeatureUnavailableError

router = APIRouter(prefix="/v1/media", tags=["media"])


class CreateUploadRequest(BaseModel):
    media_type: str = Field(pattern="^(audio|image|pdf)$")
    size_bytes: int = Field(gt=0)


class UploadResponse(BaseModel):
    media_id: str
    storage_key: str
    status: str
    upload_url: str | None


class FinalizeRequest(BaseModel):
    checksum: str = Field(min_length=8, max_length=64)
    actual_bytes: int = Field(gt=0)


class MediaResponse(BaseModel):
    media_id: str
    media_type: str
    status: str
    scan_state: str


@router.post("/uploads", response_model=UploadResponse, status_code=201)
async def create_upload(
    body: CreateUploadRequest,
    current_user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> UploadResponse:
    service = MediaService(session)
    media = await service.register_upload(
        user_id=current_user.id, media_type=body.media_type, size_bytes=body.size_bytes
    )
    await session.commit()
    # Signed upload URL: storage adapter menunggu DEC-15 — belum tersedia.
    upload_url = getattr(request.app.state, "media_upload_url_factory", None)
    return UploadResponse(
        media_id=media.id,
        storage_key=media.storage_key,
        status=media.status,
        upload_url=upload_url(media.storage_key) if callable(upload_url) else None,
    )


@router.post("/{media_id}:complete", response_model=MediaResponse)
async def finalize_upload(
    media_id: str,
    body: FinalizeRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> MediaResponse:
    service = MediaService(session)
    media = await service.finalize_upload(
        user_id=current_user.id,
        media_id=media_id,
        checksum=body.checksum,
        actual_bytes=body.actual_bytes,
    )
    await session.commit()
    return MediaResponse(
        media_id=media.id,
        media_type=media.media_type,
        status=media.status,
        scan_state=media.scan_state,
    )


@router.get("/{media_id}/download-url")
async def get_download_url(
    media_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, str]:
    service = MediaService(session)
    media = await service.get_media(user_id=current_user.id, media_id=media_id)
    factory = getattr(request.app.state, "media_download_url_factory", None)
    if not callable(factory):
        raise FeatureUnavailableError(
            "Signed URL storage belum dikonfigurasi (menunggu DEC-15).",
        )
    return {"download_url": factory(media.storage_key)}
