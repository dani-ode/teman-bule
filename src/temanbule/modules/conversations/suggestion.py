"""Suggestion message workflow transport: 3 contoh pesan untuk user via Langflow.

Flow ini dipicu dari conversation room saat user (terutama yang baru belajar)
meminta patokan pesan. Backend mengirim konteks session (session_id +
category_id + history ringkas) ke flow ``suggestion_message`` dan menerima
3 teks suggestion. Tidak ada persistensi pesan — suggestion bersifat
ephemeral, bukan bagian dari ``conversation_messages``.

Komponen canvas target ``tweaks`` tidak di-hardcode — diambil dari registry
(``FlowBinding.input_tweak_component``, kolom ``ai_flow_registry.input_tweak_component``)
agar per-environment dapat dipin tanpa mengubah kode.
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


class SuggestionResult(BaseModel):
    """Hasil flow suggestion: tepat 3 teks pesan contoh untuk user."""

    model_config = ConfigDict(extra="ignore")

    suggestions: list[str] = Field(min_length=1, max_length=3)


class _SuggestionMessages(BaseModel):
    model_config = ConfigDict(extra="ignore")

    first_message: str
    second_message: str
    third_message: str


def _extract_suggestions(output: dict[str, Any]) -> list[str]:
    """Ekstrak 3 teks suggestion dari output flow (toleran variasi struktur).

    Struktur utama yang diharapkan:
    ``data.primaryResult.content.suggestions.{first,second,third}_message``

    Beberapa deployment flow mengembalikan variasi (mis. tanpa wrapper
    ``data`` atau ``primaryResult``); fungsi ini mencoba beberapa path
    sebelum menyerah.
    """
    candidates: list[dict[str, Any]] = []

    # Path 1: data.primaryResult.content.suggestions
    data = output.get("data")
    if isinstance(data, dict):
        primary = data.get("primaryResult")
        if isinstance(primary, dict):
            content = primary.get("content")
            if isinstance(content, dict):
                suggestions = content.get("suggestions")
                if isinstance(suggestions, dict):
                    candidates.append(suggestions)

    # Path 2: primaryResult.content.suggestions (tanpa wrapper data)
    primary = output.get("primaryResult")
    if isinstance(primary, dict):
        content = primary.get("content")
        if isinstance(content, dict):
            suggestions = content.get("suggestions")
            if isinstance(suggestions, dict):
                candidates.append(suggestions)

    # Path 3: content.suggestions (langsung di root)
    content = output.get("content")
    if isinstance(content, dict):
        suggestions = content.get("suggestions")
        if isinstance(suggestions, dict):
            candidates.append(suggestions)

    # Path 4: suggestions langsung di root
    suggestions = output.get("suggestions")
    if isinstance(suggestions, dict):
        candidates.append(suggestions)

    for candidate in candidates:
        first = candidate.get("first_message")
        second = candidate.get("second_message")
        third = candidate.get("third_message")
        if (
            isinstance(first, str) and first.strip()
            and isinstance(second, str) and second.strip()
            and isinstance(third, str) and third.strip()
        ):
            return [first.strip(), second.strip(), third.strip()]

    raise ValueError("Suggestion messages not found in output")


class LangflowSuggestionAdapter:
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

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> SuggestionResult:
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
                logger.warning(
                    "suggestion workflow tidak completed: %r",
                    str(body.get("errors") if isinstance(body, dict) else body)[:600],
                )
                raise ValueError("Workflow not completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Workflow errors")
            output = parse_workflow_output(body.get("output"))
            if not isinstance(output, dict):
                raise ValueError("Workflow output invalid")
            # Output flow membungkus suggestions dalam struktur
            # data.primaryResult.content.suggestions.{first,second,third}_message
            suggestions = _extract_suggestions(output)
            result = SuggestionResult(suggestions=suggestions)
            if not any(text.strip() for text in result.suggestions):
                raise ValueError("Empty suggestion text")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("suggestion workflow gagal: %r", exc, exc_info=False)
            raise DependencyUnavailableError(
                "Suggestion pesan belum dapat dikonfirmasi.",
                code="SUGGESTION_OUTCOME_UNKNOWN",
            ) from exc
