"""Session-create workflow transport: build session + first AI message via Langflow.

Berbeda dengan chat turn biasa, flow ini hanya menerima konteks awal
(agent/category) dan mengembalikan sapaan pertama AI. Backend tetap menjadi
pemilik persistensi: teks dari flow disimpan sebagai ConversationMessage
role='agent' oleh ChatService.

Komponen canvas target ``tweaks`` tidak di-hardcode — diambil dari registry
(``FlowBinding.input_tweak_component``, kolom ``ai_flow_registry.input_tweak_component``)
agar per-environment dapat dipin tanpa mengubah kode.
"""

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.langflow import parse_workflow_output
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class SessionCreateResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response_text: str = Field(min_length=1, max_length=32000)
    # URL audio S3/MinIO hasil TTS dari workflow; opsional (fallback text-only).
    # Flat fields (bukan nested object) sesuai output Langflow greeting.
    audio_url: str | None = Field(default=None, max_length=2048)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    object_key: str | None = Field(default=None, max_length=512)
    bucket: str | None = Field(default=None, max_length=128)


class LangflowSessionCreateAdapter:
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

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> SessionCreateResult:
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
            if not isinstance(output, dict):
                raise ValueError("Workflow output invalid")
            result = SessionCreateResult.model_validate(output)
            if not result.response_text.strip():
                raise ValueError("Empty greeting text")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise DependencyUnavailableError(
                "Greeting session baru belum dapat dikonfirmasi.",
                code="SESSION_GREETING_UNKNOWN",
            ) from exc
