"""BYOK credentials router (Phase 2): register, select, revoke.

API key diterima sekali, dienkripsi sebelum persist, tidak pernah dikembalikan.
Verifier konkret menunggu DEC-08; endpoint gagal eksplisit bila belum terpasang.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, SecretStr

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.modules.catalog.byok import ByokCredentialService
from temanbule.platform.crypto import FieldCipher
from temanbule.platform.errors import FeatureUnavailableError

router = APIRouter(prefix="/v1/me/ai-credentials", tags=["byok"])


class RegisterCredentialRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=26)
    api_key: SecretStr = Field(min_length=8)
    base_url: str | None = Field(default=None, max_length=500)


class CredentialResponse(BaseModel):
    credential_id: str
    provider_id: str
    status: str
    fingerprint: str
    verified_at: str | None


class SelectModelRequest(BaseModel):
    capability: str = Field(pattern="^(llm|stt)$")
    credential_id: str = Field(min_length=1, max_length=26)
    model_id: str = Field(min_length=1, max_length=26)


class SelectModelResponse(BaseModel):
    capability: str
    credential_id: str
    model_id: str


def _build_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> ByokCredentialService:
    verifier = getattr(request.app.state, "credential_verifier", None)
    if verifier is None or not hasattr(verifier, "verify"):
        raise FeatureUnavailableError(
            "Verifikasi credential provider belum dikonfigurasi (menunggu DEC-08).",
        )
    cipher = FieldCipher(settings.crypto_key_encryption_key)
    return ByokCredentialService(session, cipher, verifier)


@router.post("", response_model=CredentialResponse, status_code=201)
async def register_credential(
    body: RegisterCredentialRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> CredentialResponse:
    service = _build_service(request, session, settings)
    credential = await service.register_credential(
        user_id=current_user.id,
        provider_id=body.provider_id,
        api_key=body.api_key.get_secret_value(),
        base_url=body.base_url,
    )
    await session.commit()
    return CredentialResponse(
        credential_id=credential.id,
        provider_id=credential.provider_id,
        status=credential.status,
        fingerprint=credential.fingerprint,
        verified_at=credential.verified_at.isoformat() if credential.verified_at else None,
    )


@router.put("/selections", response_model=SelectModelResponse)
async def select_model(
    body: SelectModelRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> SelectModelResponse:
    service = _build_service(request, session, settings)
    selection = await service.select_model(
        user_id=current_user.id,
        capability=body.capability,
        credential_id=body.credential_id,
        model_id=body.model_id,
    )
    await session.commit()
    return SelectModelResponse(
        capability=selection.capability,
        credential_id=selection.credential_id,
        model_id=selection.model_id,
    )


@router.delete("/{credential_id}", status_code=204)
async def revoke_credential(
    credential_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> None:
    service = _build_service(request, session, settings)
    await service.revoke_credential(user_id=current_user.id, credential_id=credential_id)
    await session.commit()
