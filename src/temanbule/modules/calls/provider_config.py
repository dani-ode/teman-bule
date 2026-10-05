"""Provider config loader untuk realtime worker (Phase 6 / DEC-14).

Memetakan runtime snapshot otoritatif (plan + model refs + agent version)
menjadi konfigurasi provider yang dipakai worker untuk membangun pipeline
STT/LLM/TTS:

- VIP: model STT/LLM diambil dari platform settings (``VIP_STT_*`` /
  ``VIP_LLM_*``), divalidasi terhadap katalog provider aktif di DB.
- Advance (BYOK): model berasal dari snapshot (selection pengguna) dan
  wajib divalidasi aktif; credential TIDAK dibaca di sini — plaintext key
  hanya diberikan caller melalui ``CredentialProvider`` saat job berjalan.
- Provider/model harus ada di katalog DB aktif, bukan string bebas
  (billing-plans.md). Provider di luar katalog → ConfigurationError.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    AiModelConfiguration,
    ProviderCatalog,
    RuntimeSnapshot,
)
from temanbule.platform.errors import NotFoundError
from temanbule.platform.settings import ConfigurationError, Settings

SUPPORTED_LLM_PROVIDERS = frozenset({"gemini", "openai"})
SUPPORTED_STT_PROVIDERS = frozenset({"gemini", "openai"})

# Provider TTS platform (bukan BYOK; billing-plans.md: TTS = platform expense).
SUPPORTED_TTS_PROVIDERS = frozenset({"elevenlabs"})


class CredentialProvider(Protocol):
    """Penyedia plaintext credential BYOK per attempt (one-use).

    Implementasi nyata milik runtime worker (credential broker); loader ini
    tidak pernah membaca/menyimpan plaintext key.
    """

    def api_key_for(self, credential_ref: str) -> str: ...


@dataclass(frozen=True)
class RealtimeProviderConfig:
    """Konfigurasi provider untuk satu call session; tanpa plaintext secret."""

    session_id: str
    room_name: str
    agent_code: str
    agent_display_name: str
    persona_instructions: str
    voice_id: str
    tts_provider: str
    tts_model: str
    tts_api_key: str  # platform key ElevenLabs (settings, bukan DB)
    stt_provider: str
    stt_model: str
    llm_provider: str
    llm_model: str
    byok_credential_ref: str | None


def _voice_id_for_agent(settings: Settings, agent_code: str) -> str:
    voice = settings.tts_voice_id_elean if agent_code == "elean" else settings.tts_voice_id_willy
    if not voice.strip():
        raise ConfigurationError([f"TTS_VOICE_ID_{agent_code.upper()} wajib diisi"])
    return voice


def _provider_code(model: AiModelConfiguration, provider: ProviderCatalog | None) -> str:
    if provider is None or provider.status != "active":
        raise ConfigurationError(["Provider model tidak aktif di katalog"])
    return provider.code


async def load_realtime_provider_config(
    session: AsyncSession,
    settings: Settings,
    *,
    session_id: str,
) -> RealtimeProviderConfig:
    """Bangun konfigurasi provider dari snapshot otoritatif satu call session."""
    from temanbule.modules.calls.models import CallSession
    from temanbule.modules.conversations.models import ConversationSession

    call = (
        await session.execute(
            select(CallSession).where(CallSession.session_id == session_id)
        )
    ).scalar_one_or_none()
    if call is None:
        raise NotFoundError("Call tidak ditemukan.")
    conversation = (
        await session.execute(
            select(ConversationSession).where(ConversationSession.id == session_id)
        )
    ).scalar_one()
    snapshot = (
        await session.execute(
            select(RuntimeSnapshot).where(
                RuntimeSnapshot.id == conversation.runtime_snapshot_id
            )
        )
    ).scalar_one()
    agent_version = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.id == snapshot.agent_version_id)
        )
    ).scalar_one_or_none()
    if agent_version is None:
        raise ConfigurationError(["Agent version snapshot tidak valid"])
    agent = (
        await session.execute(select(Agent).where(Agent.id == agent_version.agent_id))
    ).scalar_one()

    # --- Model STT/LLM ---
    if snapshot.llm_model_configuration_id is not None:
        # Advance (BYOK): model dari snapshot selection pengguna.
        llm_model = (
            await session.execute(
                select(AiModelConfiguration).where(
                    AiModelConfiguration.id == snapshot.llm_model_configuration_id,
                    AiModelConfiguration.status == "active",
                )
            )
        ).scalar_one_or_none()
        if llm_model is None:
            raise ConfigurationError(["LLM model configuration snapshot tidak aktif"])
        stt_model = (
            await session.execute(
                select(AiModelConfiguration).where(
                    AiModelConfiguration.id == snapshot.stt_model_configuration_id,
                    AiModelConfiguration.status == "active",
                )
            )
        ).scalar_one_or_none()
        if stt_model is None:
            raise ConfigurationError(["STT model configuration snapshot tidak aktif"])
        llm_provider = (
            await session.execute(
                select(ProviderCatalog).where(ProviderCatalog.id == llm_model.provider_id)
            )
        ).scalar_one_or_none()
        stt_provider = (
            await session.execute(
                select(ProviderCatalog).where(ProviderCatalog.id == stt_model.provider_id)
            )
        ).scalar_one_or_none()
        llm_provider_code = _provider_code(llm_model, llm_provider)
        stt_provider_code = _provider_code(stt_model, stt_provider)
        llm_identifier = llm_model.identifier
        stt_identifier = stt_model.identifier
        # CATATAN: credential BYOK untuk media call diresolve lewat credential
        # broker one-use (FND-09) yang belum tersedia untuk runtime worker;
        # panggilan BYOK gagal eksplisit di worker sampai broker itu ada.
        byok_ref = snapshot.credential_record_ref
    else:
        # VIP: platform key dari settings; divalidasi terhadap katalog aktif.
        if not settings.vip_llm_provider.strip() or not settings.vip_llm_model.strip():
            raise ConfigurationError(["VIP_LLM_PROVIDER dan VIP_LLM_MODEL wajib diisi"])
        if not settings.vip_stt_provider.strip() or not settings.vip_stt_model.strip():
            raise ConfigurationError(["VIP_STT_PROVIDER dan VIP_STT_MODEL wajib diisi"])
        catalog_model = (
            await session.execute(
                select(AiModelConfiguration)
                .join(ProviderCatalog, AiModelConfiguration.provider_id == ProviderCatalog.id)
                .where(
                    ProviderCatalog.code == settings.vip_llm_provider,
                    ProviderCatalog.status == "active",
                    AiModelConfiguration.identifier == settings.vip_llm_model,
                    AiModelConfiguration.status == "active",
                )
            )
        ).scalar_one_or_none()
        if catalog_model is None:
            raise ConfigurationError(
                ["VIP_LLM_MODEL tidak ditemukan pada katalog provider aktif"]
            )
        llm_provider_code = settings.vip_llm_provider
        stt_provider_code = settings.vip_stt_provider
        llm_identifier = settings.vip_llm_model
        stt_identifier = settings.vip_stt_model
        byok_ref = None

    if llm_provider_code not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigurationError([f"Provider LLM tidak didukung: {llm_provider_code}"])
    if stt_provider_code not in SUPPORTED_STT_PROVIDERS:
        raise ConfigurationError([f"Provider STT tidak didukung: {stt_provider_code}"])
    if settings.tts_provider not in SUPPORTED_TTS_PROVIDERS:
        raise ConfigurationError([f"Provider TTS tidak didukung: {settings.tts_provider}"])
    if not settings.tts_api_key.strip() or not settings.tts_model.strip():
        raise ConfigurationError(["TTS_API_KEY dan TTS_MODEL wajib diisi"])

    persona_ref = agent_version.persona_artifact_ref
    if not persona_ref or not persona_ref.strip():
        raise ConfigurationError(["Persona artifact agent version kosong"])

    return RealtimeProviderConfig(
        session_id=session_id,
        room_name=call.room_name,
        agent_code=agent.code,
        agent_display_name=agent.display_name,
        persona_instructions=persona_ref,
        voice_id=_voice_id_for_agent(settings, agent.code),
        tts_provider=settings.tts_provider,
        tts_model=settings.tts_model,
        tts_api_key=settings.tts_api_key,
        stt_provider=stt_provider_code,
        stt_model=stt_identifier,
        llm_provider=llm_provider_code,
        llm_model=llm_identifier,
        byok_credential_ref=byok_ref,
    )
