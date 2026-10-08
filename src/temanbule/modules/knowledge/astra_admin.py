"""Astra DB admin client: deleteMany via Data API untuk vector projections.

Astra DB dashboard tidak menyediakan hapus baris; endpoint internal memakai
client ini untuk menghapus dokumen projection berdasar metadata filter
(owner_user_id, document_id, podcast_id, agent_id, dst.).

Batasan keamanan (DEC-09):
- Credential admin (Astra token) hanya dari settings backend; tidak pernah
  diterima dari argumen caller dan tidak pernah dilog.
- Koleksi target WAJIB berasal dari vector_collection_registry SQL
  (status active) — nama koleksi fisik tidak pernah diambil dari input
  caller mentah.
- Filter WAJIB minimal satu field metadata non-empty; delete tanpa filter
  (wipe koleksi) ditolak.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from temanbule.platform.errors import DependencyUnavailableError, ValidationError

# Field metadata projection Astra yang boleh dipakai sebagai filter delete.
# Selaras dengan dokumen projection embedding_adapter._build_document() plus
# field scope-specific (owner_user_id, document_id, podcast_id, agent_id)
# yang ditambahkan ingestion path saat context job membawa field tersebut.
ALLOWED_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "owner_user_id",
        "document_id",
        "canonical_chunk_id",
        "podcast_id",
        "agent_id",
        "source_type",
        "source_id",
        "source_version",
        "content_hash",
        "embedding_profile_id",
        "projection_generation",
    }
)

# Field yang juga ditulis sebagai sub-field objek ``metadata`` oleh writer
# Langflow (dokumen Langflow menaruh custom field di bawah ``metadata``,
# bukan top-level). Filter untuk field ini harus mencakup kedua bentuk
# (top-level dan metadata.<field>) agar match campuran writer.
# Sub-field di luar daftar ini tidak boleh di-filter lewat metadata.*.
LANGFLOW_METADATA_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "owner_user_id",
        "user_owner_id",
        "document_id",
        "podcast_id",
        "agent_id",
        "chunk_id",
        "canonical_chunk_id",
        "source_version",
        "content_hash",
        "embedding_profile_id",
    }
)

_MAX_DELETE_MANY_LIMIT = 1000


@dataclass(frozen=True)
class AstraAdminConfig:
    """Konfigurasi trusted dari settings backend; tanpa nilai dari caller."""

    astra_api_endpoint: str
    astra_application_token: str
    astra_namespace: str
    request_timeout_seconds: float


class AstraAdminClient:
    """Client admin Astra Data API untuk operasi deleteMany ter-filter."""

    def __init__(
        self,
        config: AstraAdminConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.astra_api_endpoint.startswith("https://"):
            raise ValueError("Astra API endpoint must use HTTPS")
        if not config.astra_application_token.strip():
            raise ValueError("Astra application token kosong")
        if not config.astra_namespace.strip():
            raise ValueError("Astra namespace kosong")
        self._config = config
        self._transport = transport

    async def delete_many(
        self,
        *,
        collection: str,
        filters: dict[str, str],
    ) -> int:
        """Hapus dokumen di ``collection`` yang match filter (AND antar field).

        Tiap field divalidasi ketat lalu diperluas ke bentuk komposit ``$or``
        (top-level + ``metadata.<field>``) bila field itu juga ditulis writer
        Langflow — lihat ``_expand_filter``. Return jumlah dokumen terhapus
        (Data API: status.deletedCount; -1 berarti lebih dari batas internal
        Data API — dikembalikan apa adanya).
        """
        cleaned = self._validate_filters(filters)
        base = self._config.astra_api_endpoint.rstrip("/")
        url = f"{base}/api/json/v1/{self._config.astra_namespace}/{collection}"
        body = await self._request_json(
            url=url,
            json_body={"deleteMany": {"filter": self._expand_filter(cleaned)}},
            failure_label="Astra deleteMany",
        )
        status = body.get("status")
        if not isinstance(status, dict):
            raise DependencyUnavailableError(
                "Astra deleteMany response tidak memuat status.",
                code="ASTRA_INVALID_RESPONSE",
            )
        deleted = status.get("deletedCount")
        if not isinstance(deleted, int):
            raise DependencyUnavailableError(
                "Astra deleteMany response tidak memuat deletedCount.",
                code="ASTRA_INVALID_RESPONSE",
            )
        if deleted > _MAX_DELETE_MANY_LIMIT:
            # Data API membatasi deletedCount maks 1000 per panggilan; lebih
            # dari itu truncation terjadi dan caller harus mengulangi call.
            return deleted
        return deleted

    @staticmethod
    def _validate_filters(filters: dict[str, str]) -> dict[str, str]:
        if not filters:
            raise ValidationError(
                "Filter kosong; delete tanpa filter (wipe koleksi) ditolak."
            )
        cleaned: dict[str, str] = {}
        details: list[dict[str, Any]] = []
        for field, value in filters.items():
            if field not in ALLOWED_FILTER_FIELDS:
                details.append(
                    {"field": field, "message": "Field filter tidak diizinkan."}
                )
                continue
            if not isinstance(value, str) or not value.strip():
                details.append(
                    {"field": field, "message": "Nilai filter wajib string non-empty."}
                )
                continue
            cleaned[field] = value.strip()
        if details:
            raise ValidationError("Filter delete tidak valid.", details=details)
        if not cleaned:
            raise ValidationError("Minimal satu filter metadata wajib diisi.")
        return cleaned

    @staticmethod
    def _expand_filter(filters: dict[str, str]) -> dict[str, Any]:
        """Perluas tiap field ke klausa ``$or`` top-level + metadata.<field>.

        Koleksi bisa berisi campuran dua bentuk dokumen: writer backend
        (field top-level) dan writer Langflow (custom field di bawah objek
        ``metadata``). Field yang tidak ditulis Langflow tetap exact-match
        top-level. Hasil akhir: AND antar field, OR antar bentuk per field.
        """
        clauses: list[dict[str, Any]] = []
        for field, value in filters.items():
            if field in LANGFLOW_METADATA_FILTER_FIELDS:
                clauses.append({"$or": [{field: value}, {f"metadata.{field}": value}]})
            else:
                clauses.append({field: value})
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    async def _request_json(
        self,
        *,
        url: str,
        json_body: dict[str, Any],
        failure_label: str,
    ) -> dict[str, Any]:
        """HTTP JSON call; response error vendor tidak pernah diekspos mentah."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Cassandra-Token": self._config.astra_application_token,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._config.request_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(url, headers=headers, json=json_body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DependencyUnavailableError(
                f"{failure_label} transport gagal.",
                code="ASTRA_TRANSPORT_FAILED",
            ) from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                f"{failure_label} transport gagal.",
                code="ASTRA_TRANSPORT_FAILED",
            ) from exc

        if response.status_code >= 400:
            raise DependencyUnavailableError(
                f"{failure_label} ditolak vendor (HTTP {response.status_code}).",
                code="ASTRA_VENDOR_REJECTED",
            )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DependencyUnavailableError(
                f"{failure_label} response bukan JSON valid.",
                code="ASTRA_INVALID_RESPONSE",
            ) from exc
        if not isinstance(body, dict):
            raise DependencyUnavailableError(
                f"{failure_label} response envelope tidak valid.",
                code="ASTRA_INVALID_RESPONSE",
            )
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            raise DependencyUnavailableError(
                f"{failure_label} ditolak vendor (error response).",
                code="ASTRA_VENDOR_REJECTED",
            )
        return body


def build_astra_admin_client(settings: Any) -> AstraAdminClient:
    """Bangun client dari settings trusted; ValueError bila config wajib kosong."""
    return AstraAdminClient(
        AstraAdminConfig(
            astra_api_endpoint=settings.astra_db_api_endpoint,
            astra_application_token=settings.astra_db_application_token,
            astra_namespace=settings.astra_db_namespace,
            request_timeout_seconds=float(settings.astra_request_timeout_seconds),
        )
    )
