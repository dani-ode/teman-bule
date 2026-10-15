"""STT workflow transport: transkripsi voice message via Langflow (mode sync).

Dipakai endpoint ``POST /v1/media/{media_id}:transcribe`` pada alur 3-hit
client: upload → transcribe → kirim pesan (media_id + text). Dengan alur ini,
flow ``chat_turn`` tidak lagi melakukan STT — teks user tiba di backend
sebelum chat turn dipanggil, sehingga kolom ``text`` pesan user berisi
transkrip final sejak INSERT (tidak ada placeholder, tidak ada UPDATE).

Komponen canvas target ``tweaks`` tidak di-hardcode — diambil dari registry
(``FlowBinding.input_tweak_component``) agar per-environment dapat dipin
tanpa mengubah kode.
"""

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.langflow import parse_workflow_output
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


class SttResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str
    text: str = Field(min_length=1, max_length=32000)


class LangflowSttAdapter:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    @staticmethod
    def _input_component(binding: FlowBinding) -> str:
        """Komponen canvas target ``tweaks`` wajib dipin di registry."""
        component = (binding.input_tweak_component or "").strip()
        if not component:
            raise DependencyUnavailableError(
                "Registry flow belum mem-pin input_tweak_component.",
                code="FLOW_NOT_CONFIGURED",
            )
        return component

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> SttResult:
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError("Langflow belum dikonfigurasi.")
        input_component = self._input_component(binding)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    min(binding.timeout_ms / 1000, self.settings.langflow_timeout_seconds),
                    connect=self.settings.langflow_connect_timeout_seconds,
                ),
                transport=self.transport, follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.post(
                    self.settings.langflow_base_url.rstrip("/") + "/api/v2/workflows",
                    headers={"x-api-key": self.settings.langflow_api_key},
                    json={"flow_id": binding.flow_id, "mode": "sync",
                          "session_id": envelope["request_id"],
                          "tweaks": {input_component: {"data": json.dumps(envelope)}}},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "completed":
                logger.warning(
                    "stt workflow tidak completed: %r",
                    str(body.get("errors") if isinstance(body, dict) else body)[:600],
                )
                raise ValueError("Workflow not completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Workflow errors")
            output = parse_workflow_output(body.get("output"))
            result = SttResult.model_validate(output)
            if result.request_id != envelope["request_id"] or not result.text.strip():
                raise ValueError("Correlation or text invalid")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("stt workflow gagal: %r", exc, exc_info=False)
            raise DependencyUnavailableError(
                "Hasil transkripsi belum dapat dikonfirmasi.", code="STT_OUTCOME_UNKNOWN",
            ) from exc
