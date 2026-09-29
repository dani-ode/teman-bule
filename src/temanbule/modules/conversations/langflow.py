"""Strict Workflow v2 transport for a structured chat turn."""

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class ChatResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1"]
    request_id: str
    status: Literal["completed"]
    response_text: str = Field(min_length=1, max_length=32000)
    citations: list[dict[str, Any]]
    tool_outcomes: list[dict[str, Any]]
    audio: dict[str, Any] | None = None
    background_dispatch_ref: str | None = None


class LangflowChatAdapter:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> ChatResult:
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError("Langflow belum dikonfigurasi.")
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
                          "tweaks": {"json_input": {"payload": json.dumps(envelope)}}},
                )
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("status") != "completed":
                raise ValueError("Workflow not completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Workflow errors")
            output = body.get("output")
            if isinstance(output, dict) and isinstance(output.get("text"), str):
                output = json.loads(output["text"])
            result = ChatResult.model_validate(output)
            if result.request_id != envelope["request_id"] or not result.response_text.strip():
                raise ValueError("Correlation or text invalid")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise DependencyUnavailableError(
                "Hasil workflow chat belum dapat dikonfirmasi.", code="CHAT_OUTCOME_UNKNOWN",
            ) from exc
