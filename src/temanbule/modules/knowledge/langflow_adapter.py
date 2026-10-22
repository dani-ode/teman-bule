"""Extraction adapter konkret: Langflow Workflow API v2 (conversation_ingestion).

Kontrak (langflow-flows.md, services.py ExtractionPort):
- POST /api/v2/workflows, flow_id di body, ``mode="background"`` — flow
  ingestion berjalan async di Langflow dan menulis hasil canonical langsung
  ke Postgres via tool MCP (``conversation.persist_extraction``); backend
  cukup menerima acceptance ``job_id`` (fire-and-forget, tanpa polling).
  Path v1 hanya untuk konfigurasi rollback eksplisit; tidak ada fallback
  otomatis.
- Input: envelope terstruktur (schema_version/flow_version/session/messages)
  diserialisasi sebagai JSON string pada ``tweaks[<input_component>].data``;
  nama komponen Webhook target dipin per environment di
  ``ai_flow_registry.input_tweak_component`` (bukan hardcode di kode).
- API key hanya dipakai server-side via header ``x-api-key`` dan tidak pernah
  dilog. Tidak ada fake dispatch: semua kegagalan (timeout/network/HTTP/
  acceptance tidak valid) → DependencyUnavailableError sehingga SQL job tetap
  nonterminal dan dapat di-retry — Langflow belum menerima pekerjaan.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from temanbule.modules.catalog.flows import FlowBinding, resolve_flow
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


@dataclass(frozen=True)
class LangflowExtractionConfig:
    """Konfigurasi trusted dari settings backend; tanpa nilai dari caller."""

    base_url: str
    api_key: str
    run_path: str
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 5.0
    max_connections: int = 50

    def run_url(self, flow_id: str) -> str:
        """Workflow v2 memakai satu endpoint; legacy v1 menyertakan flow ID."""
        base = self.base_url.rstrip("/")
        path = "/" + self.run_path.strip("/")
        if path == "/api/v2/workflows":
            return f"{base}{path}"
        if path == "/api/v1/run":
            return f"{base}{path}/{flow_id}"
        raise ValueError("LANGFLOW_RUN_PATH tidak didukung")


class LangflowExtractionAdapter:
    """ExtractionPort konkret ke flow conversation_ingestion via Langflow.

    Satu instance dipakai ulang oleh worker; connection pool dibatasi
    ``max_connections`` dan timeout dipisah (connect vs total) sesuai
    settings. Tidak ada retry internal — retry/completion adalah milik SQL
    job; adapter hanya melaporkan kegagalan sebagai DependencyUnavailableError.
    """

    def __init__(
        self,
        config: LangflowExtractionConfig,
        resolver: Callable[[str, str], Awaitable[FlowBinding]],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._resolver = resolver

    async def extract(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        source_start: int,
        source_end: int,
        schema_version: str,
        flow_version: str,
    ) -> str:
        """Dispatch flow conversation_ingestion mode background; return ``job_id``.

        Envelope RAMPING — hanya reference (session_id + rentang sequence +
        versi schema/flow + owner); isi pesan TIDAK dikirim. Flow menarik
        pesan langsung dari Postgres via tool MCP ``conversation.get_messages``
        (pull), lalu menulis hasil canonical via ``conversation.persist_extraction``.
        Backend TIDAK menunggu output dan TIDAK menyimpan extraction.

        Response acceptance divalidasi ketat: ``object == "job"``,
        ``status == "queued"``, ``job_id`` wajib ada. Semua kegagalan vendor
        menghasilkan DependencyUnavailableError — tidak ada dispatch palsu,
        SQL job tetap nonterminal untuk retry.
        """
        binding = await self._resolver("conversation_ingestion", flow_version)
        input_component = (binding.input_tweak_component or "").strip()
        if not input_component:
            raise DependencyUnavailableError(
                "Registry flow conversation_ingestion belum mem-pin input_tweak_component.",
                code="FLOW_NOT_CONFIGURED",
            )
        envelope = json.dumps(
            {
                "schema_version": schema_version,
                "flow_version": flow_version,
                "session_id": session_id,
                "owner_user_id": owner_user_id,
                "source_start": source_start,
                "source_end": source_end,
            },
            sort_keys=True,
        )
        payload: dict[str, Any] = {
            "flow_id": binding.flow_id,
            "mode": "background",
            "session_id": session_id,
            "tweaks": {input_component: {"data": envelope}},
        }
        body = await self._run_flow(
            flow_id=binding.flow_id,
            payload=payload,
            timeout_seconds=binding.timeout_ms / 1000,
        )
        return self._validate_acceptance(body)

    async def run_purpose_flow(
        self,
        *,
        purpose: str,
        input_data: dict[str, Any],
        session_id: str,
    ) -> dict[str, Any]:
        """Jalankan flow background per purpose (facts/assessment); return dict.

        Flow ID di-resolve dari registry per purpose/version. Kegagalan vendor →
        DependencyUnavailableError (job tetap nonterminal); output non-JSON
        dikembalikan sebagai ``{"raw_text": ...}`` agar tetap canonical.
        """
        flow_version = input_data.get("flow_version")
        if not isinstance(flow_version, str) or not flow_version.strip():
            raise DependencyUnavailableError(
                "Job Langflow harus menyertakan flow_version.",
                code="FLOW_VERSION_MISSING",
            )
        binding = await self._resolver(purpose, flow_version)
        payload: dict[str, Any] = {
            "input_value": json.dumps(input_data, sort_keys=True),
            "input_type": "chat",
            "output_type": "chat",
            "session_id": session_id,
            "tweaks": {},
        }
        body = await self._run_flow(
            flow_id=binding.flow_id, payload=payload,
            timeout_seconds=binding.timeout_ms / 1000,
        )
        text = self._extract_output_text(body)
        return self._parse_extraction_text(text)

    # --- HTTP ------------------------------------------------------------

    async def _run_flow(
        self, *, flow_id: str, payload: dict[str, Any], timeout_seconds: float,
    ) -> dict[str, Any]:
        """POST run endpoint; mapping error teruniform tanpa echo body vendor."""
        workflow_v2 = self._config.run_path.strip("/") == "api/v2/workflows"
        if workflow_v2:
            # The deployed v2 schema forbids the legacy v1 type selectors.
            payload = {
                key: value for key, value in payload.items()
                if key not in {"input_type", "output_type"}
            }
            # Caller yang menentukan mode (sync untuk purpose flow lama,
            # background untuk ingestion dispatch); default sync.
            payload = {**payload, "flow_id": flow_id}
            payload.setdefault("mode", "sync")
        timeout = httpx.Timeout(
            min(timeout_seconds, self._config.timeout_seconds),
            connect=self._config.connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=self._config.max_connections,
            max_keepalive_connections=self._config.max_connections,
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self._config.api_key,
        }
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._config.run_url(flow_id),
                    headers=headers,
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DependencyUnavailableError(
                "Langflow extraction transport gagal; job tetap nonterminal.",
                code="LANGFLOW_TRANSPORT_FAILED",
            ) from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "Langflow extraction transport gagal.",
                code="LANGFLOW_TRANSPORT_FAILED",
            ) from exc

        if response.status_code >= 400:
            # Body error vendor tidak diekspos (bisa memuat detail sensitif).
            raise DependencyUnavailableError(
                f"Langflow extraction ditolak (HTTP {response.status_code}).",
                code="LANGFLOW_RUN_REJECTED",
            )

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DependencyUnavailableError(
                "Langflow extraction response bukan JSON valid.",
                code="LANGFLOW_INVALID_RESPONSE",
            ) from exc
        if not isinstance(body, dict):
            raise DependencyUnavailableError(
                "Langflow extraction response envelope tidak valid.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        if workflow_v2 and payload.get("mode") == "sync" and (
            body.get("status") != "completed"
            or body.get("has_errors", False) is not False
            or bool(body.get("errors"))
            or not isinstance(body.get("output"), dict)
        ):
            raise DependencyUnavailableError(
                "Langflow workflow belum berhasil selesai.", code="LANGFLOW_INVALID_RESPONSE"
            )
        return body

    @staticmethod
    def _validate_acceptance(body: dict[str, Any]) -> str:
        """Validasi acceptance mode background; return ``job_id`` Langflow.

        Response valid: ``{"object": "job", "status": "queued", "job_id": ...}``.
        Acceptance bukan completion — flow tetap bisa gagal nanti, tetapi
        pekerjaan sudah diterima vendor sehingga SQL job dapat dianggap sukses
        (reconciliation memakai vendor_job_id pada checkpoint/attempt).
        """
        if body.get("object") != "job":
            raise DependencyUnavailableError(
                "Langflow ingestion acceptance envelope tidak valid.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        if body.get("status") != "queued" or body.get("errors"):
            raise DependencyUnavailableError(
                "Langflow ingestion tidak di-accept.",
                code="LANGFLOW_RUN_REJECTED",
            )
        job_id = body.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise DependencyUnavailableError(
                "Langflow ingestion acceptance tanpa job_id.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        return job_id

    # --- Response parsing --------------------------------------------------

    @staticmethod
    def _extract_output_text(body: dict[str, Any]) -> str:
        """Ambil teks output utama dari struktur run response v1.

        Struktur: outputs[0].outputs[0].results.message.text, dengan fallback
        ``results.message.data.text`` dan ``artifacts.message`` untuk variasi
        komponen output. Struktur tidak dikenal → DependencyUnavailableError.
        """
        if "output" in body:
            output = body["output"]
            workflow_text = output.get("text") if isinstance(output, dict) else None
            if not isinstance(workflow_text, str) or not workflow_text.strip():
                raise DependencyUnavailableError(
                    "Langflow workflow tanpa teks output.", code="LANGFLOW_INVALID_RESPONSE"
                )
            return workflow_text
        outputs = body.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise DependencyUnavailableError(
                "Langflow extraction response tanpa outputs.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        first_session = outputs[0]
        inner = first_session.get("outputs") if isinstance(first_session, dict) else None
        if not isinstance(inner, list) or not inner:
            raise DependencyUnavailableError(
                "Langflow extraction response tanpa output component.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        first_output = inner[0] if isinstance(inner[0], dict) else None
        results = first_output.get("results") if first_output is not None else None
        message = results.get("message") if isinstance(results, dict) else None

        text: Any = None
        if isinstance(message, dict):
            text = message.get("text")
            if text is None and isinstance(message.get("data"), dict):
                text = message["data"].get("text")
        if text is None and isinstance(first_output, dict):
            artifacts = first_output.get("artifacts")
            if isinstance(artifacts, dict):
                text = artifacts.get("message")

        if not isinstance(text, str) or not text.strip():
            raise DependencyUnavailableError(
                "Langflow extraction response tanpa teks output.",
                code="LANGFLOW_INVALID_RESPONSE",
            )
        return text

    @staticmethod
    def _parse_extraction_text(text: str) -> dict[str, Any]:
        """Parse teks output sebagai JSON object; fallback ``raw_text``.

        Flow mengembalikan JSON canonical (summary/evidence); bila output
        bukan JSON object valid, teks mentah dibungkus ``{"raw_text": ...}``
        agar tetap tersimpan canonical tanpa mengarang struktur.
        """
        try:
            parsed = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return {"raw_text": text}
        if isinstance(parsed, dict):
            return parsed
        return {"raw_text": text}


def build_extraction_adapter(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession],
) -> LangflowExtractionAdapter:
    """Bangun adapter dari settings trusted; ValueError bila config wajib kosong."""
    required_strings = {
        "LANGFLOW_BASE_URL": settings.langflow_base_url,
        "LANGFLOW_API_KEY": settings.langflow_api_key,
        "LANGFLOW_RUN_PATH": settings.langflow_run_path,
    }
    missing = [name for name, value in required_strings.items() if not value.strip()]
    if missing:
        raise ValueError(
            "Konfigurasi Langflow extraction adapter tidak lengkap: "
            + ", ".join(sorted(missing))
        )
    async def resolver(purpose: str, flow_version: str) -> FlowBinding:
        async with session_factory() as session:
            return await resolve_flow(
                session, environment=settings.app_env,
                purpose=purpose, flow_version=flow_version,
            )

    return LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url=settings.langflow_base_url,
            api_key=settings.langflow_api_key,
            run_path=settings.langflow_run_path,
            timeout_seconds=float(settings.langflow_timeout_seconds),
            connect_timeout_seconds=float(settings.langflow_connect_timeout_seconds),
            max_connections=settings.langflow_max_connections,
        ),
        resolver=resolver,
    )
