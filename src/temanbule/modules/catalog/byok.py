"""BYOK credential service (Phase 2): encrypted storage + verification + selection.

Aturan (billing-plans.md, postgresql-schema.md):
- API key disimpan terenkripsi (FieldCipher); fingerprint SHA-256 tanpa secret.
- Credential harus terverifikasi ke provider sebelum aktif (verification port;
  konkret menunggu DEC-08, tanpa menebak API vendor).
- Selection (llm/stt) menunjuk credential milik user + model aktif provider sama.
- Tidak ada fallback ke key admin bila BYOK gagal.
- SSRF: custom base_url hanya bila provider allows_custom_base_url; kebijakan
  jaringan egress ditegakkan adapter pemanggil (bukan service ini).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import (
    AiModelConfiguration,
    ProviderCatalog,
    UserAiCredential,
    UserAiSelection,
)
from temanbule.platform.crypto import FieldCipher
from temanbule.platform.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid, sha256_hex

CAPABILITY_LLM = "llm"
CAPABILITY_STT = "stt"

STATUS_PENDING = "pending_verification"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_FAILED = "verification_failed"


class CredentialVerificationPort(Protocol):
    """Port verifikasi credential ke provider. Konkret menunggu DEC-08."""

    async def verify(
        self, *, provider_code: str, api_key: str, base_url: str | None
    ) -> bool: ...


class ByokCredentialService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: FieldCipher,
        verifier: CredentialVerificationPort,
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.verifier = verifier

    async def register_credential(
        self,
        *,
        user_id: str,
        provider_id: str,
        api_key: str,
        base_url: str | None = None,
    ) -> UserAiCredential:
        if not api_key.strip():
            raise ValidationError("API key kosong.")
        provider = (
            await self.session.execute(
                select(ProviderCatalog).where(
                    ProviderCatalog.id == provider_id,
                    ProviderCatalog.status == "active",
                )
            )
        ).scalar_one_or_none()
        if provider is None:
            raise NotFoundError("Provider tidak aktif.")
        if base_url is not None and not provider.allows_custom_base_url:
            raise ValidationError(
                "Provider tidak mengizinkan custom base_url.",
                details=[{"field": "base_url", "message": "ditolak kebijakan provider"}],
            )

        verified = await self.verifier.verify(
            provider_code=provider.code, api_key=api_key, base_url=base_url
        )
        credential = UserAiCredential(
            id=new_ulid(),
            user_id=user_id,
            provider_id=provider.id,
            encrypted_api_key=self.cipher.encrypt(api_key),
            encrypted_base_url=self.cipher.encrypt(base_url) if base_url else None,
            fingerprint=sha256_hex(api_key),
            status=STATUS_ACTIVE if verified else STATUS_FAILED,
            verified_at=datetime.now(UTC) if verified else None,
        )
        self.session.add(credential)
        await self.session.flush()
        if not verified:
            raise ConflictError(
                "Credential gagal diverifikasi ke provider.",
                code="BYOK_VERIFICATION_FAILED",
            )
        return credential

    async def revoke_credential(self, *, user_id: str, credential_id: str) -> None:
        credential = await self._owned_credential(user_id, credential_id)
        if credential.status == STATUS_REVOKED:
            return
        credential.status = STATUS_REVOKED
        credential.revoked_at = datetime.now(UTC)
        await self.session.flush()
        # Selection yang menunjuk credential ini dihapus agar tidak dipakai lagi.
        selections = (
            (
                await self.session.execute(
                    select(UserAiSelection).where(
                        UserAiSelection.credential_id == credential_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for selection in selections:
            await self.session.delete(selection)
        await self.session.flush()

    async def select_model(
        self,
        *,
        user_id: str,
        capability: str,
        credential_id: str,
        model_id: str,
    ) -> UserAiSelection:
        if capability not in (CAPABILITY_LLM, CAPABILITY_STT):
            raise ValidationError(
                "Capability tidak valid.",
                details=[{"field": "capability", "message": "harus llm atau stt"}],
            )
        credential = await self._owned_credential(user_id, credential_id)
        if credential.status != STATUS_ACTIVE or credential.revoked_at is not None:
            raise ConflictError(
                "Credential tidak aktif.",
                code="BYOK_CREDENTIAL_INVALID",
            )
        model = (
            await self.session.execute(
                select(AiModelConfiguration).where(
                    AiModelConfiguration.id == model_id,
                    AiModelConfiguration.status == "active",
                )
            )
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError("Model tidak aktif.")
        if model.provider_id != credential.provider_id:
            raise ConflictError(
                "Model bukan milik provider credential.",
                code="BYOK_PROVIDER_MISMATCH",
            )

        existing = (
            await self.session.execute(
                select(UserAiSelection).where(
                    UserAiSelection.user_id == user_id,
                    UserAiSelection.capability == capability,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.credential_id = credential.id
            existing.model_id = model.id
            await self.session.flush()
            return existing
        selection = UserAiSelection(
            user_id=user_id,
            capability=capability,
            credential_id=credential.id,
            model_id=model.id,
        )
        self.session.add(selection)
        await self.session.flush()
        return selection

    async def get_decrypted_key(self, *, user_id: str, credential_id: str) -> str:
        """Untuk adapter invocation saja; tidak pernah masuk log/snapshot."""
        credential = await self._owned_credential(user_id, credential_id)
        if credential.status != STATUS_ACTIVE or credential.revoked_at is not None:
            raise ConflictError(
                "Credential tidak dapat dipakai.",
                code="BYOK_CREDENTIAL_INVALID",
            )
        return self.cipher.decrypt(credential.encrypted_api_key)

    async def _owned_credential(self, user_id: str, credential_id: str) -> UserAiCredential:
        credential = (
            await self.session.execute(
                select(UserAiCredential).where(UserAiCredential.id == credential_id)
            )
        ).scalar_one_or_none()
        if credential is None:
            raise NotFoundError("Credential tidak ditemukan.")
        if credential.user_id != user_id:
            raise ForbiddenError("Credential bukan milik user.")
        return credential
