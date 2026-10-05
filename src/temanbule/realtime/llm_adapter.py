"""OpenAI-compatible LLM adapter untuk realtime worker (DEC-14).

Subclass ``livekit.agents.llm.LLM`` tanpa plugin vendor: berbicara streaming
ke endpoint chat-completions yang kompatibel OpenAI (Gemini OpenAI-compat
endpoint, OpenAI asli) sesuai provider katalog DB.

Konfigurasi ``base_url``/``api_key`` WAJIB eksplisit dari settings (VIP) atau
credential broker BYOK — tanpa env fallback tersembunyi dan tanpa default
tertanam (public.md: no hardcoding, fail-fast). Tool/function calling per-turn
ditolak eksplisit: eksekusi tool adalah jalur CallCraft (M3), bukan voice
pipeline.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from livekit.agents import llm
from livekit.agents.llm import ChatContext
from livekit.agents.llm.chat_context import ChatRole
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

_OPENAI_COMPAT_PATHS: dict[str, str] = {
    "gemini": "/v1beta/openai/chat/completions",
    "openai": "/chat/completions",
}


def chat_completions_url(provider: str, base_url: str) -> str:
    try:
        path = _OPENAI_COMPAT_PATHS[provider]
    except KeyError as exc:
        raise ValueError(f"Provider LLM tidak didukung: {provider}") from exc
    return base_url.rstrip("/") + path


def _message_payload(role: ChatRole, text: str) -> dict[str, Any]:
    mapped = {
        "system": "system",
        "developer": "developer",
        "user": "user",
        "assistant": "assistant",
    }[role]
    return {"role": mapped, "content": text}


class OpenAICompatibleLLM(llm.LLM[Any]):
    """LLM streaming minimal untuk endpoint OpenAI-compatible."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
    ) -> None:
        super().__init__()
        if not provider.strip() or not model.strip() or not base_url.strip() or not api_key.strip():
            raise ValueError("provider/model/base_url/api_key wajib untuk LLM adapter")
        self._provider_name = provider
        self._model_name = model
        self._url = chat_completions_url(provider, base_url)
        self._api_key = api_key
        self._timeout = timeout_seconds

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def provider(self) -> str:
        return self._provider_name

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: Any = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        if tools:
            raise NotImplementedError(
                "Tool calling per-turn belum diaktifkan untuk voice pipeline; "
                "eksekusi tool melalui CallCraft."
            )
        messages = [
            _message_payload(item.role, item.raw_text_content or "")
            for item in chat_ctx.messages()
        ]
        body: dict[str, Any] = {"model": self._model_name, "messages": messages, "stream": True}
        if isinstance(extra_kwargs, dict):
            body.update(extra_kwargs)
        return _OpenAICompatStream(
            self,
            chat_ctx=chat_ctx,
            url=self._url,
            api_key=self._api_key,
            body=body,
            timeout_seconds=self._timeout,
        )


class _OpenAICompatStream(llm.LLMStream):
    """SSE streaming chat-completions → ChatChunk; body provider tidak dilog."""

    def __init__(
        self,
        llm_instance: OpenAICompatibleLLM,
        *,
        chat_ctx: ChatContext,
        url: str,
        api_key: str,
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> None:
        super().__init__(
            llm_instance,
            chat_ctx=chat_ctx,
            tools=[],
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        self._url = url
        self._api_key = api_key
        self._body = body
        self._timeout = timeout_seconds

    async def _run(self) -> None:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self._timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", self._url, headers=headers, json=self._body
            ) as response:
                if response.status_code != 200:
                    # Jangan membaca/meneruskan body provider (risiko detail sensitif).
                    raise httpx.HTTPStatusError(
                        "LLM provider menolak request.",
                        request=response.request,
                        response=response,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices", []):
                        content = (choice.get("delta") or {}).get("content")
                        if content:
                            self._event_ch.send_nowait(
                                llm.ChatChunk(
                                    id=str(chunk.get("id", "")),
                                    delta=llm.ChoiceDelta(role="assistant", content=content),
                                )
                            )
