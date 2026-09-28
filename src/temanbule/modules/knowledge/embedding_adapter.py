"""Embedding adapter konkret (DEC-09): Gemini + OpenAI → Astra DB projections.

Kontrak (astra-collections.md, langflow-flows.md):
- Satu chunk canonical di-embed ke provider sesuai profile, lalu vector
  di-upsert idempoten ke physical collection Astra milik (scope, provider,
  profile). Tidak ada fake vector; semua kegagalan → DependencyUnavailableError
  sehingga SQL job tetap nonterminal dan dapat di-retry per branch.
- Panjang vector WAJIB persis profile.dimension — vector salah dimensi tidak
  pernah disimpan (ruang provider/model/revision tidak boleh tercampur).
- ``_id`` dokumen deterministik: sha256(canonical_chunk_id | source_version |
  profile_id | generation) sesuai kontrak "hash dari canonical chunk ID +
  source version + embedding profile + projection generation". Upsert dengan
  ``_id`` sama meng-overwrite dokumen lama → retry branch aman (idempoten).
- Metadata identity/filter (chunk_id, content_hash, profile, dimension)
  diisi komponen trusted ini, bukan dari output model.
- Credential admin (API key provider + Astra token) hanya dari settings
  backend; tidak pernah dilog dan tidak diterima dari argumen caller.

Resolution profile: baris SQL ORM ``EmbeddingProfile`` tidak membawa provider
code/model identifier/physical collection name. Worker memakai
``resolve_embedding_profile()`` untuk mengubah baris SQL + provider/model
catalog + vector_collection_registry menjadi ``EmbeddingProfileSpec`` milik
modul ini, lalu meneruskannya sebagai ``profile`` ke ``embed()`` (Protocol
EmbeddingPort duck-typed; spec menyediakan atribut konkret yang dibutuhkan).
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import AiModelConfiguration, ProviderCatalog
from temanbule.modules.knowledge.models import (
    EmbeddingProfile,
    VectorCollectionRegistry,
)
from temanbule.platform.errors import DependencyUnavailableError, NotFoundError
from temanbule.platform.security import sha256_hex
from temanbule.platform.settings import Settings

SCHEMA_VERSION_PROJECTION = "embedding_projection.v1"

_PROVIDER_GEMINI = "gemini"
_PROVIDER_OPENAI = "openai"
_SUPPORTED_PROVIDERS = {_PROVIDER_GEMINI, _PROVIDER_OPENAI}

# Batas retry hanya untuk kegagalan transient (timeout/network/5xx/429).
_RETRY_BASE_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class EmbeddingProfileSpec:
    """Profile embedding yang sudah di-resolve ke atribut konkret provider.

    Field mengikuti kebutuhan EmbeddingPort: identitas target (id/generation/
    dimension), routing provider (provider code + model identifier + revision),
    task type dokumen, dan physical collection Astra dari registry.
    """

    id: str
    provider: str  # provider_catalog.code: "gemini" | "openai"
    model: str  # ai_model_configurations.identifier
    model_revision: int
    dimension: int
    document_task_type: str
    generation: int
    physical_collection_name: str  # vector_collection_registry.physical_name


async def resolve_embedding_profile(
    session: AsyncSession,
    *,
    profile_id: str,
    environment: str,
    scope: str,
) -> EmbeddingProfileSpec:
    """Resolve baris SQL EmbeddingProfile → spec konkret untuk adapter.

    Menggabungkan provider_catalog.code, ai_model_configurations.identifier
    dan vector_collection_registry.physical_name (binding environment+profile
    berstatus active). Gagal 404 bila profile/binding tidak ditemukan — bukan
    DependencyUnavailableError, karena ini kesalahan state canonical, bukan
    kegagalan vendor.
    """
    row = (
        await session.execute(
            select(EmbeddingProfile, ProviderCatalog, AiModelConfiguration)
            .join(ProviderCatalog, EmbeddingProfile.provider_id == ProviderCatalog.id)
            .join(AiModelConfiguration, EmbeddingProfile.model_id == AiModelConfiguration.id)
            .where(EmbeddingProfile.id == profile_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Embedding profile tidak ditemukan.")
    profile, provider, model = row

    physical_name = (
        await session.execute(
            select(VectorCollectionRegistry.physical_name).where(
                VectorCollectionRegistry.environment == environment,
                VectorCollectionRegistry.scope == scope,
                VectorCollectionRegistry.profile_id == profile.id,
                VectorCollectionRegistry.status == "active",
            )
        )
    ).scalar_one_or_none()
    if physical_name is None:
        raise NotFoundError("Physical collection registry belum aktif untuk profile ini.")

    return EmbeddingProfileSpec(
        id=profile.id,
        provider=provider.code,
        model=model.identifier,
        model_revision=profile.model_revision,
        dimension=profile.dimension,
        document_task_type=profile.document_task_type,
        generation=profile.generation,
        physical_collection_name=physical_name,
    )


@dataclass(frozen=True)
class DualEmbeddingConfig:
    """Konfigurasi trusted dari settings backend; tanpa nilai dari caller."""

    gemini_api_key: str
    gemini_base_url: str
    openai_api_key: str
    openai_base_url: str
    astra_api_endpoint: str
    astra_application_token: str
    astra_namespace: str
    max_attempts: int
    request_timeout_seconds: float


class DualEmbeddingAdapter:
    """EmbeddingPort konkret: embed satu chunk + upsert idempoten ke Astra.

    Satu instance melayani kedua provider; routing berdasarkan
    ``profile.provider``. Retry ringan (max_attempts) hanya untuk error
    transient; 4xx auth/validation langsung gagal. Tidak ada fallback/mock.
    """

    def __init__(
        self,
        config: DualEmbeddingConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        for base_url in (config.gemini_base_url, config.openai_base_url):
            if not base_url.startswith("https://"):
                raise ValueError("Embedding provider base URL must use HTTPS")
        if not config.astra_api_endpoint.startswith("https://"):
            raise ValueError("Astra API endpoint must use HTTPS")
        if config.max_attempts < 1:
            raise ValueError("embedding_max_attempts minimal 1")
        self._config = config
        self._transport = transport

    # --- Port entry point -------------------------------------------------

    async def embed(
        self,
        *,
        chunk_text: str,
        profile: EmbeddingProfileSpec,
        chunk_id: str,
        source_version: str,
        content_hash: str,
    ) -> str:
        """Embed satu chunk lalu upsert ke Astra; return vector_id (``_id``).

        ``_id`` deterministik per (chunk, source_version, profile, generation)
        sehingga retry branch menimpa dokumen yang sama (idempoten). Vector
        salah dimensi → DependencyUnavailableError, tidak disimpan.
        """
        if not chunk_text.strip():
            raise DependencyUnavailableError(
                "Chunk text kosong; embedding ditolak.",
                code="EMBEDDING_INPUT_INVALID",
            )
        if profile.provider == _PROVIDER_GEMINI:
            vector = await self._embed_gemini(chunk_text=chunk_text, profile=profile)
        elif profile.provider == _PROVIDER_OPENAI:
            vector = await self._embed_openai(chunk_text=chunk_text, profile=profile)
        else:
            raise DependencyUnavailableError(
                "Provider embedding tidak didukung.",
                code="EMBEDDING_PROVIDER_UNSUPPORTED",
            )

        self._validate_vector(vector=vector, expected_dimension=profile.dimension)

        vector_id = deterministic_vector_id(
            chunk_id=chunk_id,
            source_version=source_version,
            profile_id=profile.id,
            generation=profile.generation,
        )
        await self._upsert_astra(
            collection=profile.physical_collection_name,
            document=self._build_document(
                vector_id=vector_id,
                vector=vector,
                chunk_text=chunk_text,
                chunk_id=chunk_id,
                source_version=source_version,
                content_hash=content_hash,
                profile=profile,
            ),
        )
        return vector_id

    # --- Provider calls ----------------------------------------------------

    async def _embed_gemini(
        self, *, chunk_text: str, profile: EmbeddingProfileSpec
    ) -> list[float]:
        """Gemini embedContent API (taskType document sesuai profile)."""
        base = self._config.gemini_base_url.rstrip("/")
        url = f"{base}/v1beta/models/{profile.model}:embedContent"
        payload = {
            "model": f"models/{profile.model}",
            "content": {"parts": [{"text": chunk_text}]},
            "taskType": profile.document_task_type,
            "outputDimensionality": profile.dimension,
        }
        body = await self._request_json(
            method="POST",
            url=url,
            params={"key": self._config.gemini_api_key},
            json_body=payload,
            failure_label="Gemini embedding",
        )
        embedding = body.get("embedding")
        values = embedding.get("values") if isinstance(embedding, dict) else None
        if not isinstance(values, list):
            raise DependencyUnavailableError(
                "Gemini embedding response tidak memuat embedding.values.",
                code="EMBEDDING_INVALID_RESPONSE",
            )
        vector = self._coerce_float_vector(values)
        # Reduced Gemini embeddings require normalization before cosine indexing.
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm == 0:
            raise DependencyUnavailableError(
                "Gemini embedding norm tidak valid.", code="EMBEDDING_INVALID_RESPONSE"
            )
        return [value / norm for value in vector]

    async def _embed_openai(
        self, *, chunk_text: str, profile: EmbeddingProfileSpec
    ) -> list[float]:
        """OpenAI embeddings API; ``dimensions`` eksplisit sesuai profile."""
        base = self._config.openai_base_url.rstrip("/")
        payload: dict[str, Any] = {
            "model": profile.model,
            "input": chunk_text,
            "dimensions": profile.dimension,
        }
        body = await self._request_json(
            method="POST",
            url=f"{base}/embeddings",
            headers={"Authorization": f"Bearer {self._config.openai_api_key}"},
            json_body=payload,
            failure_label="OpenAI embedding",
        )
        data = body.get("data")
        first = data[0] if isinstance(data, list) and data else None
        vector = first.get("embedding") if isinstance(first, dict) else None
        if not isinstance(vector, list):
            raise DependencyUnavailableError(
                "OpenAI embedding response tidak memuat data[0].embedding.",
                code="EMBEDDING_INVALID_RESPONSE",
            )
        return self._coerce_float_vector(vector)

    # --- Astra upsert ------------------------------------------------------

    async def _upsert_astra(self, *, collection: str, document: dict[str, Any]) -> None:
        """insertOne ke Astra Data API; idempoten via ``_id`` deterministik."""
        base = self._config.astra_api_endpoint.rstrip("/")
        url = f"{base}/api/json/v1/{self._config.astra_namespace}/{collection}"
        await self._request_json(
            method="POST",
            url=url,
            headers={"X-Cassandra-Token": self._config.astra_application_token},
            json_body={"insertOne": {"document": document}},
            failure_label="Astra upsert",
        )

    def _build_document(
        self,
        *,
        vector_id: str,
        vector: list[float],
        chunk_text: str,
        chunk_id: str,
        source_version: str,
        content_hash: str,
        profile: EmbeddingProfileSpec,
    ) -> dict[str, Any]:
        """Dokumen projection sesuai kontrak astra-collections.md.

        Metadata identity diisi komponen trusted ini. Scope/owner/filter
        khusus scope (agent_version_ids, podcast_id, dll.) ditambahkan oleh
        lanjutan kontrak DEC-09 saat context job membawa field tersebut;
        field inti wajib selalu ada di sini.
        """
        return {
            "_id": vector_id,
            "$vector": vector,
            "text": chunk_text,
            "canonical_chunk_id": chunk_id,
            "source_version": source_version,
            "content_hash": content_hash,
            "embedding_profile_id": profile.id,
            "embedding_model_revision": profile.model_revision,
            "dimension": profile.dimension,
            "schema_version": SCHEMA_VERSION_PROJECTION,
            "projection_generation": profile.generation,
            "created_at": datetime.now(UTC).isoformat(),
        }

    # --- HTTP + retry -------------------------------------------------------

    async def _request_json(
        self,
        *,
        method: str,
        url: str,
        json_body: dict[str, Any],
        failure_label: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """HTTP JSON call dengan retry transient + mapping error teruniform.

        Transient (timeout/network/429/5xx) di-retry dengan backoff linear;
        4xx lain (auth/validation) langsung gagal. Response error vendor tidak
        pernah diekspos mentah (bisa memuat echo secret); hanya status code.
        """
        merged_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        last_exc: DependencyUnavailableError | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._config.request_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=merged_headers,
                        params=params,
                        json=json_body,
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = DependencyUnavailableError(
                    f"{failure_label} transport gagal (attempt {attempt}).",
                    code="EMBEDDING_TRANSPORT_FAILED",
                )
                last_exc.__cause__ = exc
                await self._sleep_before_retry(attempt)
                continue
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError(
                    f"{failure_label} transport gagal.",
                    code="EMBEDDING_TRANSPORT_FAILED",
                ) from exc

            if response.status_code in (429,) or response.status_code >= 500:
                last_exc = DependencyUnavailableError(
                    f"{failure_label} vendor transient HTTP {response.status_code}.",
                    code="EMBEDDING_VENDOR_TRANSIENT",
                )
                await self._sleep_before_retry(attempt)
                continue
            if response.status_code >= 400:
                raise DependencyUnavailableError(
                    f"{failure_label} ditolak vendor (HTTP {response.status_code}).",
                    code="EMBEDDING_VENDOR_REJECTED",
                )

            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise DependencyUnavailableError(
                    f"{failure_label} response bukan JSON valid.",
                    code="EMBEDDING_INVALID_RESPONSE",
                ) from exc
            if not isinstance(body, dict):
                raise DependencyUnavailableError(
                    f"{failure_label} response envelope tidak valid.",
                    code="EMBEDDING_INVALID_RESPONSE",
                )
            return body

        if last_exc is None:  # unreachable: max_attempts >= 1 dijamin __init__
            raise DependencyUnavailableError(
                f"{failure_label} gagal tanpa response vendor.",
                code="EMBEDDING_TRANSPORT_FAILED",
            )
        raise last_exc

    async def _sleep_before_retry(self, attempt: int) -> None:
        """Backoff linear sederhana; attempt terakhir tidak menunggu."""
        if attempt >= self._config.max_attempts:
            return
        await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * attempt)

    # --- Vector validation ---------------------------------------------------

    @staticmethod
    def _coerce_float_vector(values: list[Any]) -> list[float]:
        """Konversi ketat ke float; nilai non-numerik → invalid response."""
        vector: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DependencyUnavailableError(
                    "Embedding response memuat nilai non-numerik.",
                    code="EMBEDDING_INVALID_RESPONSE",
                )
            vector.append(float(value))
        return vector

    @staticmethod
    def _validate_vector(*, vector: list[float], expected_dimension: int) -> None:
        """Panjang harus persis dimension dan semua nilai finite.

        Vector salah dimensi tidak pernah disimpan (ruang provider/model
        tidak boleh tercampur walaupun dimensi kebetulan sama).
        """
        if expected_dimension <= 0:
            raise DependencyUnavailableError(
                "Embedding profile dimension tidak valid.",
                code="EMBEDDING_DIMENSION_MISMATCH",
            )
        if len(vector) != expected_dimension or not all(math.isfinite(v) for v in vector):
            raise DependencyUnavailableError(
                "Embedding dimension tidak sesuai profile; vector ditolak.",
                code="EMBEDDING_DIMENSION_MISMATCH",
            )


def deterministic_vector_id(
    *, chunk_id: str, source_version: str, profile_id: str, generation: int
) -> str:
    """``_id`` Astra stabil: sha256(chunk | source_version | profile | generation)."""
    identity = f"{chunk_id}|{source_version}|{profile_id}|{generation}"
    return sha256_hex(identity)


def build_embedding_adapter(settings: Settings) -> DualEmbeddingAdapter:
    """Bangun adapter dari settings trusted; ValueError bila config wajib kosong."""
    required_strings = {
        "GEMINI_API_KEY": settings.gemini_api_key,
        "GEMINI_BASE_URL": settings.gemini_base_url,
        "OPENAI_API_KEY": settings.openai_api_key,
        "OPENAI_BASE_URL": settings.openai_base_url,
        "ASTRA_DB_API_ENDPOINT": settings.astra_db_api_endpoint,
        "ASTRA_DB_APPLICATION_TOKEN": settings.astra_db_application_token,
        "ASTRA_DB_NAMESPACE": settings.astra_db_namespace,
    }
    missing = [name for name, value in required_strings.items() if not value.strip()]
    if settings.embedding_max_attempts < 1:
        missing.append("EMBEDDING_MAX_ATTEMPTS")
    if missing:
        raise ValueError(
            "Konfigurasi embedding adapter tidak lengkap: " + ", ".join(sorted(missing))
        )
    return DualEmbeddingAdapter(
        DualEmbeddingConfig(
            gemini_api_key=settings.gemini_api_key,
            gemini_base_url=settings.gemini_base_url,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
            astra_api_endpoint=settings.astra_db_api_endpoint,
            astra_application_token=settings.astra_db_application_token,
            astra_namespace=settings.astra_db_namespace,
            max_attempts=settings.embedding_max_attempts,
            request_timeout_seconds=float(settings.astra_request_timeout_seconds),
        )
    )
