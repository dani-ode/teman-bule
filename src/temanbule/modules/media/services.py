"""Media service (Phase 5): upload lifecycle, scan, retention, voice note STT.

Kontrak (implementation-plan.md Phase 5, api-events.md, realtime-podcast.md):
- Upload finalize hanya untuk owner; checksum wajib cocok; oversized/invalid
  ditolak eksplisit; malicious → rejected, tidak dapat dipakai.
- Voice note STT lewat plan terpilih (VIP platform / Advance BYOK) via
  SttPort; transcript → chat retry-safe (client_key dedupe pada messages).
- Scanner/S3 konkret menunggu DEC-15; storage/scan sebagai port.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.services import ConversationService
from temanbule.modules.media.models import MediaObject
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

STATUS_PENDING_UPLOAD = "pending_upload"
STATUS_UPLOADED = "uploaded"
STATUS_FINALIZED = "finalized"
STATUS_REJECTED = "rejected"

SCAN_PENDING = "pending"
SCAN_CLEAN = "clean"
SCAN_MALICIOUS = "malicious"

# Batas ukuran per tipe (policy baseline; angka final mengikuti DEC-15)
MAX_BYTES_BY_TYPE: dict[str, int] = {
    "audio": 25 * 1024 * 1024,
    "image": 10 * 1024 * 1024,
    "pdf": 50 * 1024 * 1024,
}


class MalwareScannerPort(Protocol):
    """Port scanner; konkret menunggu DEC-15."""

    async def scan(self, *, storage_key: str, media_type: str) -> str:
        """Return 'clean' | 'malicious' | 'error'."""
        ...


class SttPort(Protocol):
    """Port STT voice note (flow voice_note_transcription). Konkret menunggu DEC-08/10."""

    async def transcribe(self, *, media: MediaObject, language: str) -> str: ...


class MediaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_upload(
        self, *, user_id: str, media_type: str, size_bytes: int
    ) -> MediaObject:
        if media_type not in MAX_BYTES_BY_TYPE:
            raise ValidationError(
                "media_type tidak didukung.",
                details=[{"field": "media_type", "message": media_type}],
            )
        max_bytes = MAX_BYTES_BY_TYPE[media_type]
        if size_bytes <= 0 or size_bytes > max_bytes:
            raise ValidationError(
                "Ukuran media di luar batas.",
                details=[{"field": "bytes", "message": f"maks {max_bytes}"}],
            )
        media = MediaObject(
            id=new_ulid(),
            owner_user_id=user_id,
            storage_key=f"media/{user_id}/{new_ulid()}",
            media_type=media_type,
            bytes=size_bytes,
            checksum="",  # diisi saat finalize
        )
        self.session.add(media)
        await self.session.flush()
        return media

    async def finalize_upload(
        self, *, user_id: str, media_id: str, checksum: str, actual_bytes: int
    ) -> MediaObject:
        """Finalize hanya owner; checksum/bytes harus cocok dengan deklarasi."""
        media = await self._owned_media(user_id, media_id, for_update=True)
        if media.status == STATUS_FINALIZED:
            return media
        if media.status != STATUS_PENDING_UPLOAD:
            raise ConflictError(
                "Media tidak dapat difinalisasi dari status saat ini.",
                code="MEDIA_STATE_INVALID",
            )
        if not checksum.strip():
            raise ValidationError("checksum wajib.")
        if actual_bytes != media.bytes:
            raise ValidationError(
                "Ukuran aktual tidak cocok dengan deklarasi.",
                details=[{"field": "bytes", "message": "mismatch"}],
            )
        media.checksum = checksum
        media.status = STATUS_UPLOADED
        await self.session.flush()
        return media

    async def apply_scan_result(
        self, *, media_id: str, scan_state: str
    ) -> MediaObject:
        """Terapkan hasil scan (dipanggil worker setelah scanner port)."""
        media = (
            await self.session.execute(
                select(MediaObject).where(MediaObject.id == media_id).with_for_update()
            )
        ).scalar_one_or_none()
        if media is None:
            raise NotFoundError("Media tidak ditemukan.")
        if scan_state not in (SCAN_CLEAN, SCAN_MALICIOUS, "error"):
            raise ValidationError("scan_state tidak valid.")
        media.scan_state = scan_state
        media.status = STATUS_FINALIZED if scan_state == SCAN_CLEAN else STATUS_REJECTED
        await self.session.flush()
        return media

    async def transcribe_voice_note_to_chat(
        self,
        *,
        user_id: str,
        media_id: str,
        session_id: str,
        language: str,
        stt: SttPort,
    ) -> str:
        """STT voice note → simpan transcript sebagai pesan chat (retry-safe).

        Retry-safe: client_key deterministik per media+session mencegah
        transcript ganda pada retry worker.
        """
        media = await self._owned_media(user_id, media_id)
        if media.status != STATUS_FINALIZED or media.scan_state != SCAN_CLEAN:
            raise ConflictError(
                "Media belum dapat dipakai (scan/status).",
                code="MEDIA_NOT_READY",
            )
        if media.media_type != "audio":
            raise ValidationError(
                "Hanya media audio yang dapat ditranskripsi.",
                details=[{"field": "media_type", "message": media.media_type}],
            )
        transcript = await stt.transcribe(media=media, language=language)
        if not transcript.strip():
            raise ConflictError(
                "STT mengembalikan transcript kosong.",
                code="STT_EMPTY_RESULT",
            )
        conversations = ConversationService(self.session)
        client_key = f"voice-note:{media_id}"
        message = await conversations.append_user_message(
            user_id=user_id,
            session_id=session_id,
            text=transcript,
            client_key=client_key,
        )
        return message.id

    async def get_media(self, *, user_id: str, media_id: str) -> MediaObject:
        return await self._owned_media(user_id, media_id)

    async def _owned_media(
        self, user_id: str, media_id: str, *, for_update: bool = False
    ) -> MediaObject:
        stmt = select(MediaObject).where(MediaObject.id == media_id)
        if for_update:
            stmt = stmt.with_for_update()
        media = (await self.session.execute(stmt)).scalar_one_or_none()
        if media is None or media.owner_user_id != user_id:
            raise NotFoundError("Media tidak ditemukan.")
        return media
