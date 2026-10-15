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
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.langflow import (
    _STREAM_FRAME_LIMIT,
    ChatStreamEvent,
    LangflowChatAdapter,
    _map_end_vertex,
    _StreamState,
    parse_workflow_output,
)
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


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

    async def run_stream(
        self, binding: FlowBinding, envelope: dict[str, Any]
    ) -> AsyncIterator[ChatStreamEvent]:
        """Greeting session baru via Workflow API ``mode="stream"`` (SSE).

        Menghasilkan event aplikasi bertahap: ``started`` → ``text_delta``
        (teks greeting dari end_vertex LLM; SATU delta penuh selama event
        token belum tersedia di deployment) → ``usage_updated`` → tepat satu
        terminal ``completed``/``failed``.

        Output akhir divalidasi persis seperti mode sync
        (parse_workflow_output + SessionCreateResult + response_text
        non-kosong), sehingga kontrak hasil identik — hanya waktu
        tampilnya yang lebih awal.
        """
        if not self.settings.langflow_api_key:
            raise DependencyUnavailableError("Langflow belum dikonfigurasi.")
        input_component = self._input_component(binding)
        state = _StreamState(request_id=envelope["request_id"])
        yield ChatStreamEvent(
            kind="started",
            data={
                "request_id": envelope["request_id"],
                "session_id": envelope["session"]["session_id"],
                "schema_version": envelope.get("schema_version", ""),
            },
        )
        terminal: ChatStreamEvent | None = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    min(binding.timeout_ms / 1000, self.settings.langflow_timeout_seconds),
                    connect=self.settings.langflow_connect_timeout_seconds,
                ),
                transport=self.transport, follow_redirects=False, trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.langflow_base_url.rstrip("/") + "/api/v2/workflows",
                    headers={"x-api-key": self.settings.langflow_api_key},
                    json={"flow_id": binding.flow_id, "mode": "stream",
                          "session_id": envelope["session"]["session_id"],
                          "tweaks": {input_component: {"data": json.dumps(envelope)}}},
                ) as response:
                    response.raise_for_status()
                    frames = 0
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue  # abaikan id:/event:/keep-alive
                        payload = line.removeprefix("data:").strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            frame = json.loads(payload)
                        except json.JSONDecodeError:
                            continue  # baris non-JSON (mis. keep-alive prose)
                        frames += 1
                        if frames > _STREAM_FRAME_LIMIT:
                            raise ValueError("Stream frames exceeded limit")
                        if not isinstance(frame, dict):
                            continue
                        event_name = frame.get("event")
                        frame_data = frame.get("data")
                        if not isinstance(frame_data, dict):
                            frame_data = {}
                        if event_name == "end_vertex":
                            mapped = _map_end_vertex(frame_data, state)
                            if mapped is not None:
                                yield mapped
                            while state.events:
                                yield state.events.pop(0)
                        elif event_name == "output":
                            LangflowChatAdapter._capture_output_frame(frame_data, state)
                        elif event_name == "end":
                            # Frame terminal flow: bawa status + output akhir.
                            LangflowChatAdapter._capture_end_frame(frame_data, state)
            if terminal is None:
                terminal = self._terminal_from_state(state)
        except DependencyUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("greeting workflow stream gagal: %r", exc, exc_info=False)
            terminal = ChatStreamEvent(
                kind="failed",
                error="Greeting session baru belum dapat dikonfirmasi.",
                data={"code": "SESSION_GREETING_UNKNOWN"},
            )
        yield terminal

    @staticmethod
    def _terminal_from_state(state: _StreamState) -> ChatStreamEvent:
        """Bangun terminal event dari output final greeting yang terkumpul.

        Jalur utama: parse ``state.final_output`` ke ``SessionCreateResult``
        — identik mode sync.

        Aturan ketat: output akhir flow WAJIB valid (parse + SessionCreateResult
        + response_text non-kosong). Bila tidak ada / invalid, greeting
        dianggap GAGAL — tidak ada fallback rekonstruksi dari response_text.
        """
        LangflowChatAdapter._select_final_output(state)
        if state.final_output is None:
            raise ValueError("No final output in stream state")
        output = parse_workflow_output(state.final_output)
        if not isinstance(output, dict):
            raise ValueError("Greeting output invalid")
        candidate = SessionCreateResult.model_validate(output)
        if not candidate.response_text.strip():
            raise ValueError("Greeting response_text blank")
        return ChatStreamEvent(kind="completed", result=candidate)
