"""Strict Workflow v2 transport for a structured chat turn.

Komponen canvas target ``tweaks`` tidak di-hardcode — diambil dari registry
(``FlowBinding.input_tweak_component``, kolom ``ai_flow_registry.input_tweak_component``)
agar per-environment dapat dipin tanpa mengubah kode.
"""

import json
import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


def parse_workflow_output(output: Any) -> Any:
    """Bongkar output workflow menjadi dict.

    ChatOutput Langflow selalu mengembalikan JSON sebagai *string* dan sering
    membungkusnya dalam markdown fence ```json ... ```. Backend yang memformat
    ulang ke dict sebelum divalidasi model Pydantic.

    Toleransi format flow: ``citations``/``tool_outcomes`` boleh berupa JSON
    string (mis. ``"[]"``) — dinormalisasi ke list; ``status: "complete"``
    dipetakan ke ``"completed"``.
    """
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        output = output["text"]
    if isinstance(output, str):
        text = output.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        output = json.loads(text)
    if isinstance(output, dict):
        for key in ("citations", "tool_outcomes"):
            if isinstance(output.get(key), str):
                output[key] = json.loads(output[key])
        if isinstance(output.get("schema_version"), int):
            output["schema_version"] = str(output["schema_version"])
        if output.get("status") == "complete":
            output["status"] = "completed"
    return output


class ChatResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # schema_version di-echo balik oleh flow dari envelope input (versi input
    # registry), bukan versi kontrak output — cukup divalidasi sebagai string.
    schema_version: str = Field(min_length=1, max_length=40)
    request_id: str
    status: Literal["completed"]
    response_text: str = Field(min_length=1, max_length=32000)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tool_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # Flat fields (bukan nested object) konsisten dengan SessionCreateResult
    audio_url: str | None = Field(default=None, max_length=2048)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    object_key: str | None = Field(default=None, max_length=512)
    bucket: str | None = Field(default=None, max_length=128)
    background_dispatch_ref: str | None = None


class LangflowChatAdapter:
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

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> ChatResult:
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
                          "session_id": envelope["session"]["session_id"],
                          "tweaks": {input_component: {"data": json.dumps(envelope)}}},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "completed":
                raise ValueError("Workflow not completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Workflow errors")
            output = parse_workflow_output(body.get("output"))
            result = ChatResult.model_validate(output)
            if result.request_id != envelope["request_id"] or not result.response_text.strip():
                raise ValueError("Correlation or text invalid")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("chat workflow gagal: %r", exc, exc_info=False)
            raise DependencyUnavailableError(
                "Hasil workflow chat belum dapat dikonfirmasi.", code="CHAT_OUTCOME_UNKNOWN",
            ) from exc
