"""Langflow adapter podcast (Phase 7): ingestion background + script sync.

Kontrak (langflow-flows.md, realtime-podcast.md, api-events.md):
- ``podcast_document_ingestion``: mode ``background`` via Workflow API v2;
  backend menyimpan ``job_id`` Langflow pada attempt SQL dan memantau hingga
  terminal (``completed``/``failed``). Acceptance bukan completion.
- ``podcast_script_generation``: mode ``sync`` saat pengguna menekan play;
  hasil outline + segments + citations divalidasi ketat sebelum disimpan.
- Tweak diarahkan ke komponen Webhook yang dipin di registry (bukan hardcode
  di kode); nama komponen menjadi bagian kontrak deployment flow.
- API key hanya server-side via header ``x-api-key`` dan tidak pernah dilog.
  Semua kegagalan vendor → DependencyUnavailableError agar SQL job/request
  dapat di-retry; tidak ada output palsu.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

# Komponen input flow podcast (dokumentasi deployment; flow_id tetap dari registry).
PODCAST_INGESTION_WEBHOOK_COMPONENT = "Webhook-iGh25"
PODCAST_SCRIPT_WEBHOOK_COMPONENT = "Webhook-qTk7h"


class PodcastSegmentOutput(BaseModel):
    """Satu segment dialog dari flow script generation."""

    model_config = ConfigDict(extra="ignore")

    speaker: str = Field(pattern="^(elean|willy)$")
    text: str = Field(min_length=1, max_length=8000)
    citations: list[str] = Field(min_length=1)
    estimated_ms: int | None = Field(default=None, ge=0)


class PodcastScriptResult(BaseModel):
    """Output tervalidasi flow ``podcast_script_generation`` (mode sync)."""

    model_config = ConfigDict(extra="ignore")

    outline: str = Field(min_length=1, max_length=8000)
    estimated_duration_seconds: int | None = Field(default=None, ge=0)
    segments: list[PodcastSegmentOutput] = Field(min_length=2)


class LangflowPodcastAdapter:
    """Adapter Workflow API v2 untuk dua flow podcast.

    Satu instance dipakai ulang; timeout dipisah (connect vs total) dari
    settings. Tidak ada retry internal — retry adalah milik SQL job (ingestion)
    atau keputusan pengguna (script generation sync).
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    # --- Ingestion (background) -------------------------------------------

    async def trigger_document_ingestion(
        self, binding: FlowBinding, envelope: dict[str, Any]
    ) -> str:
        """Dispatch flow ingestion mode background; return ``job_id`` Langflow.

        Response acceptance divalidasi ketat: status harus ``queued`` dan
        ``job_id`` wajib ada. Kegagalan transport/HTTP/response tidak valid
        → DependencyUnavailableError (job SQL tetap nonterminal untuk retry).
        """
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError(
                "Langflow belum dikonfigurasi.", code="LANGFLOW_CONFIG_MISSING"
            )
        try:
            async with self._client(binding.timeout_ms) as client:
                response = await client.post(
                    self._workflows_url(),
                    headers=self._headers(),
                    json={
                        "flow_id": binding.flow_id,
                        "mode": "background",
                        "tweaks": {
                            PODCAST_INGESTION_WEBHOOK_COMPONENT: {
                                "data": json.dumps(envelope)
                            }
                        },
                    },
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("object") != "job":
                raise ValueError("Ingestion acceptance envelope invalid")
            if body.get("status") != "queued" or body.get("errors"):
                raise ValueError("Ingestion tidak di-accept Langflow")
            job_id = body.get("job_id")
            if not isinstance(job_id, str) or not job_id.strip():
                raise ValueError("Ingestion acceptance tanpa job_id")
            return job_id
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise DependencyUnavailableError(
                "Dispatch ingestion podcast belum dapat dikonfirmasi.",
                code="PODCAST_INGESTION_DISPATCH_UNKNOWN",
            ) from exc

    async def get_job_status(self, *, job_id: str) -> str:
        """Poll status job Langflow; return status mentah (queued/running/...).

        Hanya untuk observability/reconciliation worker — completion tetap
        ditandai oleh ``poll_document_ingestion`` yang memvalidasi output.
        """
        body = await self._poll(job_id=job_id, timeout_ms=30000)
        status = body.get("status")
        if not isinstance(status, str) or not status.strip():
            raise DependencyUnavailableError(
                "Status job Langflow tidak valid.", code="LANGFLOW_INVALID_RESPONSE"
            )
        return status

    async def poll_document_ingestion(
        self, *, job_id: str, timeout_ms: int
    ) -> dict[str, Any]:
        """Ambil hasil terminal job ingestion; return output tervalidasi.

        Sukses hanya bila status ``completed`` tanpa error dan output memuat
        ``page_count`` positif. Status non-terminal → ``PODCAST_INGESTION_RUNNING``
        (worker menjadwalkan poll ulang); status terminal gagal →
        ``PODCAST_INGESTION_FAILED`` (permanent, tidak di-retry vendor-nya).
        """
        body = await self._poll(job_id=job_id, timeout_ms=timeout_ms)
        status = body.get("status")
        if status in {"queued", "running", "pending"}:
            raise DependencyUnavailableError(
                "Ingestion podcast masih berjalan di Langflow.",
                code="PODCAST_INGESTION_RUNNING",
            )
        if status != "completed" or body.get("errors"):
            raise DependencyUnavailableError(
                f"Ingestion podcast gagal di Langflow (status={status}).",
                code="PODCAST_INGESTION_FAILED",
            )
        output = self._output_dict(body)
        page_count = output.get("page_count")
        if not isinstance(page_count, int) or page_count <= 0:
            raise DependencyUnavailableError(
                "Output ingestion podcast tanpa page_count valid.",
                code="PODCAST_INGESTION_OUTPUT_INVALID",
            )
        return output

    # --- Script generation (sync) ------------------------------------------

    async def run_script_generation(
        self, binding: FlowBinding, envelope: dict[str, Any]
    ) -> PodcastScriptResult:
        """Jalankan flow script generation mode sync; return hasil tervalidasi.

        Output wajib memuat outline + minimal dua segment berspeaker Elean/Willy
        dengan citations (paper grounding). Struktur tidak valid →
        DependencyUnavailableError; script tidak pernah dikarang.
        """
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError(
                "Langflow belum dikonfigurasi.", code="LANGFLOW_CONFIG_MISSING"
            )
        try:
            async with self._client(binding.timeout_ms) as client:
                response = await client.post(
                    self._workflows_url(),
                    headers=self._headers(),
                    json={
                        "flow_id": binding.flow_id,
                        "mode": "sync",
                        "tweaks": {
                            PODCAST_SCRIPT_WEBHOOK_COMPONENT: {
                                "data": json.dumps(envelope)
                            }
                        },
                    },
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "completed":
                raise ValueError("Script generation tidak completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Script generation errors")
            output = self._output_dict(body)
            result = PodcastScriptResult.model_validate(output)
            speakers = {segment.speaker for segment in result.segments}
            if speakers != {"elean", "willy"}:
                raise ValueError("Script wajib dua speaker Elean dan Willy")
            return result
        except (httpx.HTTPError, ValueError, ValidationError, KeyError) as exc:
            raise DependencyUnavailableError(
                "Script generation podcast belum dapat dikonfirmasi.",
                code="PODCAST_SCRIPT_OUTCOME_UNKNOWN",
            ) from exc

    # --- HTTP helpers --------------------------------------------------------

    def _workflows_url(self) -> str:
        return self.settings.langflow_base_url.rstrip("/") + "/api/v2/workflows"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.settings.langflow_api_key,
        }

    def _client(self, timeout_ms: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                min(timeout_ms / 1000, self.settings.langflow_timeout_seconds),
                connect=self.settings.langflow_connect_timeout_seconds,
            ),
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def _poll(self, *, job_id: str, timeout_ms: int) -> dict[str, Any]:
        """GET status job (links.status); mapping error teruniform."""
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError(
                "Langflow belum dikonfigurasi.", code="LANGFLOW_CONFIG_MISSING"
            )
        try:
            async with self._client(timeout_ms) as client:
                response = await client.get(
                    self._workflows_url(),
                    params={"job_id": job_id},
                    headers=self._headers(),
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "Polling job Langflow gagal.", code="LANGFLOW_TRANSPORT_FAILED"
            ) from exc
        if not isinstance(body, dict):
            raise DependencyUnavailableError(
                "Response polling Langflow tidak valid.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        return body

    @staticmethod
    def _output_dict(body: dict[str, Any]) -> dict[str, Any]:
        """Ambil output object dari response workflow; ``output.text`` JSON di-parse."""
        output = body.get("output")
        if isinstance(output, dict) and isinstance(output.get("text"), str):
            try:
                output = json.loads(output["text"])
            except (ValueError, json.JSONDecodeError) as exc:
                raise DependencyUnavailableError(
                    "Output workflow bukan JSON valid.",
                    code="LANGFLOW_INVALID_RESPONSE",
                ) from exc
        if not isinstance(output, dict):
            raise DependencyUnavailableError(
                "Workflow tanpa output object.", code="LANGFLOW_INVALID_RESPONSE"
            )
        return output
