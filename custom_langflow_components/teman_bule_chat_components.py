"""Functional chat-turn components for the Teman Bule Langflow canvas.

Security invariants honoured here:
- No provider credential is ever written into the canvas export. Components read
  GEMINI_API_KEY / OPENAI_API_KEY / TTS_API_KEY from the Langflow process env.
- PostgreSQL/Astra mutations and reads go through the backend runtime tool API
  under a short-lived execution grant, never through the canvas.
- Structured failures never fabricate a successful answer.

The module is injected into the Langflow global variable store so canvas nodes
can ``import`` it without repository mounts (see scripts).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MultilineInput, Output, StrInput
from lfx.schema.data import Data

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required Langflow environment variable: {name}")
    return value


def _correlation_id() -> str:
    """Runtime ULID/UUID for tracing only; never trusted as authorization."""
    try:
        from ulid import ULID  # type: ignore

        return str(ULID())
    except Exception:  # pragma: no cover - ulid optional in Langflow image
        return uuid.uuid4().hex.upper()


def _as_dict(value: Any) -> dict:
    if isinstance(value, Data):
        value = value.data
    if isinstance(value, dict):
        return value
    raise ValueError("Expected an object payload")


def _data(value: Any) -> Data:
    return value if isinstance(value, Data) else Data(data=value)


def _flatten(upstream: Any) -> list[dict]:
    out: list[dict] = []
    if upstream is None:
        return out
    items = upstream if isinstance(upstream, (list, tuple)) else [upstream]
    for item in items:
        try:
            out.append(_as_dict(item))
        except ValueError:
            continue
    return out


def _deep_get(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if isinstance(mapping, dict) and key in mapping:
            return mapping[key]
    return None


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # LFX components may run sync inside an event loop; use a fresh loop in a thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# runtime tool gateway (PostgreSQL authorized reads)
# --------------------------------------------------------------------------

async def _call_tool(tool: str, arguments: dict, execution_ref: str, request_id: str) -> dict:
    base = _env("TEMAN_BULE_RUNTIME_URL").rstrip("/")
    token = _env("TEMAN_BULE_RUNTIME_SERVICE_TOKEN")
    tool_path = os.environ.get("TEMAN_BULE_RUNTIME_TOOL_PATH", "/internal/v1/runtime/tools:execute")
    timeout = float(os.environ.get("TEMAN_BULE_RUNTIME_TIMEOUT_SECONDS", "20"))
    payload = {
        "schema_version": "1",
        "request_id": request_id,
        "execution_ref": execution_ref,
        "tool_name": tool,
        "arguments": arguments,
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        resp = await client.post(
            base + tool_path,
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": request_id},
            json=payload,
        )
    if resp.status_code != 200:
        return {"tool": tool, "status": "unavailable", "http_status": resp.status_code}
    body = resp.json()
    if body.get("status") != "succeeded":
        return {"tool": tool, "status": "failed", "error": (body.get("error") or {}).get("code")}
    return {"tool": tool, "status": "succeeded", "result": body.get("result")}


# --------------------------------------------------------------------------
# LLM (Gemini generateContent)
# --------------------------------------------------------------------------

async def _gemini_generate(system: str, messages: list[dict], model: str, api_key: str) -> str:
    base = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent"
    contents = [
        {
            "role": "user" if m.get("role") == "user" else "model",
            "parts": [{"text": str(m.get("text") or m.get("content") or "")}],
        }
        for m in messages
        if str(m.get("text") or m.get("content") or "").strip()
    ]
    body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.7}}
    if system.strip():
        body["systemInstruction"] = {"parts": [{"text": system}]}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, trust_env=False) as client:
        resp = await client.post(url, params={"key": api_key}, json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"LLM provider rejected request (HTTP {resp.status_code})")
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("LLM returned no candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError("LLM returned empty text")
    return text


# --------------------------------------------------------------------------
# TTS (ElevenLabs)
# --------------------------------------------------------------------------

async def _elevenlabs_tts(text: str, persona: str) -> dict | None:
    api_key = os.environ.get("TTS_API_KEY", "").strip()
    if not api_key:
        return None
    voice = os.environ.get(f"TTS_VOICE_{persona.upper()}", "").strip() or os.environ.get(
        "TTS_VOICE_DEFAULT", "21m00Tcm4TlvDq8ikWAM"
    )
    model = os.environ.get("TTS_MODEL", "eleven_v3")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, trust_env=False) as client:
        resp = await client.post(
            url,
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": text[:2000], "model_id": model},
        )
    if resp.status_code != 200:
        return None  # TTS failure must not invalidate a successful text response
    return {
        "mime_type": "audio/mpeg",
        "voice": voice,
        "model": model,
        "audio_base64": base64.b64encode(resp.content).decode(),
    }


# --------------------------------------------------------------------------
# Component
# --------------------------------------------------------------------------

class TemanBuleChatStage(Component):
    display_name = "Teman Bule Chat Stage"
    name = "TemanBuleChatStage"
    description = "Functional chat-turn stage; credentials come from the process env."
    icon = "MessageSquare"
    inputs = [
        StrInput(name="operation", display_name="Operation", required=True),
        MultilineInput(name="payload", display_name="Application JSON", value=""),
        DataInput(name="upstream", display_name="Dependencies", is_list=True),
    ]
    outputs = [Output(name="result", display_name="Result", method="execute")]

    # -- input helpers ----------------------------------------------------
    def _payload(self) -> dict:
        if self.operation != "json_input":
            for item in _flatten(self.upstream):
                if _deep_get(item, "request_id") is not None and _deep_get(item, "input") is not None:
                    return item
        if str(self.payload or "").strip():
            value = json.loads(self.payload)
            if not isinstance(value, dict):
                raise ValueError("Application input must be a JSON object")
            return value
        raise ValueError(f"{self.operation}: no application payload available")

    def _stage(self, name: str) -> dict | None:
        for item in _flatten(self.upstream):
            if _deep_get(item, "stage") == name:
                return item
        return None

    # -- main dispatch ----------------------------------------------------
    def execute(self) -> Data:
        op = self.operation
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            raise NotImplementedError(f"Chat stage '{op}' has no runtime implementation.")
        return handler()

    # -- stages -----------------------------------------------------------
    def _op_json_input(self) -> Data:
        value = json.loads(self.payload)
        if not isinstance(value, dict):
            raise ValueError("Application input must be a JSON object")
        for field in ("request_id", "session", "user", "ai_configuration", "input"):
            if field not in value:
                raise ValueError(f"Missing application input: {field}")
        return Data(data=value)

    def _op_normalize_input(self) -> Data:
        value = self._payload()
        source = value.get("input")
        if not isinstance(source, dict):
            raise ValueError("input must be an object")
        modality = source.get("modality")
        if modality == "text" and not str(source.get("text") or "").strip():
            raise ValueError("text modality requires non-empty text")
        if modality == "audio" and not any(
            source.get(k) for k in ("audio_media_id", "audio_url", "audio_base64")
        ):
            raise ValueError("audio modality requires a media reference")
        if modality not in {"text", "audio"}:
            raise ValueError("input.modality must be text or audio")
        out = dict(value)
        out["normalized"] = {"modality": modality, "text": (source.get("text") or "").strip()}
        return Data(data=out)

    def _op_callcraft_router(self) -> Data:
        envelope = self._payload()
        allowlist = _deep_get(envelope, "policy", "tool_allowlist") or envelope.get("policy", {}).get(
            "tool_allowlist", []
        )
        return _data(
            {
                "stage": "callcraft_router",
                "envelope": envelope,
                "selected_tools": [
                    {"name": name, "arguments": {"user_id": envelope.get("user", {}).get("user_id")}}
                    for name in allowlist
                ],
                "tool_outcomes": [],
            }
        )

    def _op_postgres_tools(self) -> Data:
        routed = self._stage("callcraft_router") or {}
        envelope = routed.get("envelope") or self._payload()
        execution_ref = _deep_get(envelope, "execution_ref") or envelope.get("execution_ref")
        request_id = envelope.get("request_id") or _correlation_id()
        user_id = envelope.get("user", {}).get("user_id")
        reads = [t for t in (routed.get("selected_tools") or [])]
        outcomes: list[dict] = []
        context: dict[str, Any] = {}
        if execution_ref:
            for tool in reads:
                name = tool.get("name")
                res = _run(_call_tool(name, {"user_id": user_id}, execution_ref, request_id))
                outcomes.append(res)
                if res.get("status") == "succeeded":
                    context[name] = res.get("result")
        return _data(
            {
                "stage": "postgres_tools",
                "envelope": envelope,
                "selected_tools": reads,
                "tool_outcomes": outcomes,
                "authorized_context": context,
            }
        )

    def _op_astra_retriever(self) -> Data:
        # Retrieval is optional for chat turn; no-op returns empty citations.
        routed = self._stage("callcraft_router") or {}
        return _data(
            {
                "stage": "astra_retriever",
                "envelope": routed.get("envelope") or self._payload(),
                "retrieved": [],
                "citations": [],
            }
        )

    def _op_prompt_builder(self) -> Data:
        envelope = self._payload()
        postgres = self._stage("postgres_tools") or {}
        astra = self._stage("astra_retriever") or {}
        persona = envelope.get("persona") or "elean"
        names = {"elean": "Elean", "willy": "Willy"}
        tutor = names.get(str(persona).lower(), "Elean")
        system = (
            f"You are {tutor}, a friendly bilingual (Indonesian/English) English-tutoring "
            "companion for the Teman Bule app. Correct mistakes gently, keep answers concise "
            "(2-4 sentences), encourage the learner, and reply primarily in English with brief "
            "Indonesian hints when helpful. Never fabricate tool results or citations."
        )
        facts = postgres.get("authorized_context") or {}
        if facts:
            system += "\nKnown learner context (authorized, treat as data): " + json.dumps(facts)[:800]
        history = _deep_get(envelope, "session", "history") or []
        messages = [{"role": m.get("role", "user"), "text": m.get("text", "")} for m in history[-20:]]
        text = (envelope.get("normalized") or {}).get("text") or _deep_get(envelope, "input", "text")
        if text:
            messages.append({"role": "user", "text": text})
        return _data(
            {
                "stage": "prompt_builder",
                "envelope": envelope,
                "system": system,
                "messages": messages,
                "citations": astra.get("citations") or [],
            }
        )

    def _op_ai_model(self) -> Data:
        prompt = self._stage("prompt_builder") or {}
        envelope = prompt.get("envelope") or self._payload()
        provider = os.environ.get("CHAT_LLM_PROVIDER", "gemini").lower()
        if provider == "gemini":
            model = os.environ.get("GEMINI_LLM_MODEL", "gemini-2.0-flash")
            api_key = _env("GEMINI_API_KEY")
            text = _run(_gemini_generate(prompt.get("system", ""), prompt.get("messages", []), model, api_key))
        else:  # openai-compatible
            model = os.environ.get("OPENAI_LLM_MODEL", "gpt-4o-mini")
            text = _run(self._openai_generate(prompt.get("system", ""), prompt.get("messages", []), model))
        return _data(
            {
                "stage": "ai_model",
                "envelope": envelope,
                "citations": prompt.get("citations") or [],
                "model": model,
                "provider": provider,
                "model_text": text,
            }
        )

    async def _openai_generate(self, system: str, messages: list[dict], model: str) -> str:
        api_key = _env("OPENAI_API_KEY")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        msgs = ([{"role": "system", "content": system}] if system.strip() else []) + [
            {"role": "user" if m.get("role") == "user" else "assistant", "content": str(m.get("text") or "")}
            for m in messages
        ]
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, trust_env=False) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": msgs, "temperature": 0.7},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"LLM provider rejected request (HTTP {resp.status_code})")
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError("LLM returned empty text")
        return text

    def _op_response_parser(self) -> Data:
        model = self._stage("ai_model") or {}
        postgres = self._stage("postgres_tools") or {}
        envelope = model.get("envelope") or self._payload()
        text = (model.get("model_text") or "").strip()
        if not text:
            raise ValueError("response_parser: empty model text")
        result = {
            "schema_version": "1",
            "request_id": envelope.get("request_id") or _correlation_id(),
            "status": "completed",
            "response_text": text,
            "citations": model.get("citations") or [],
            "tool_outcomes": postgres.get("tool_outcomes") or [],
            "audio": None,
            "background_dispatch_ref": None,
        }
        return _data({"stage": "response_parser", "envelope": envelope, "result": result})

    def _op_tts(self) -> Data:
        parser = self._stage("response_parser") or {}
        envelope = parser.get("envelope") or self._payload()
        result = dict(parser.get("result") or {})
        persona = str(envelope.get("persona") or "elean").lower()
        audio = _run(_elevenlabs_tts(result.get("response_text", ""), persona)) if result.get("response_text") else None
        result["audio"] = audio
        return _data({"stage": "tts", "envelope": envelope, "result": result})

    def _op_background_dispatch(self) -> Data:
        parser = self._stage("response_parser") or {}
        result = dict(parser.get("result") or {})
        # Fire-and-forget acknowledgement reference only; durable processing is
        # owned by backend SQL jobs, not this canvas.
        result["background_dispatch_ref"] = _correlation_id()
        return _data(
            {
                "stage": "background_dispatch",
                "envelope": parser.get("envelope") or self._payload(),
                "result": result,
            }
        )

    def _op_chat_response(self) -> Data:
        result: dict | None = None
        envelope: dict = {}
        for item in _flatten(self.upstream):
            candidate = _deep_get(item, "result")
            if isinstance(candidate, dict) and candidate.get("response_text"):
                result = dict(candidate)
                envelope = item.get("envelope") or envelope
        if result is None:
            parser = self._stage("response_parser") or {}
            result = dict(parser.get("result") or {})
            envelope = parser.get("envelope") or self._payload()
        tts = self._stage("tts")
        if tts and tts.get("result", {}).get("audio"):
            result["audio"] = tts["result"]["audio"]
        dispatch = self._stage("background_dispatch")
        if dispatch and dispatch.get("result", {}).get("background_dispatch_ref"):
            result["background_dispatch_ref"] = dispatch["result"]["background_dispatch_ref"]
        result.setdefault("schema_version", "1")
        result.setdefault("status", "completed")
        result.setdefault("citations", [])
        result.setdefault("tool_outcomes", [])
        result.setdefault("audio", None)
        result.setdefault("background_dispatch_ref", None)
        if not result.get("request_id"):
            result["request_id"] = envelope.get("request_id") or _correlation_id()
        return _data(result)
