"""Transport doubles untuk LangflowSessionCreateAdapter; bukan uji vendor live.

Kontrak:
- POST /api/v2/workflows mode sync, key ``tweaks`` menunjuk komponen canvas
  yang dipin di registry (``input_tweak_component``), envelope dikirim sebagai
  JSON string di field ``data`` komponen Webhook — tanpa hardcode ``json_input``.
- Output object (atau ``output.text`` JSON string) → SessionCreateResult
  tervalidasi; response_text kosong/non-completed/errors →
  SESSION_GREETING_UNKNOWN.
- Binding tanpa input_tweak_component → FLOW_NOT_CONFIGURED.
"""

from __future__ import annotations

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.session_create import LangflowSessionCreateAdapter
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

BINDING = FlowBinding(
    "greeting-flow",
    "v1",
    "1",
    "1",
    5000,
    input_tweak_component="Webhook-Nbrfi",
)

ENVELOPE = {
    "request_id": "greet-1",
    "purpose": "chat_session_create",
    "user": {"user_id": "user-1"},
    "session": {"session_id": "session-1", "runtime_snapshot_id": "snap-1"},
    "input": {"agent_code": "elean", "category_id": "cat-1"},
}


def _adapter(respond) -> LangflowSessionCreateAdapter:
    return LangflowSessionCreateAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(respond),
    )


@pytest.mark.asyncio
async def test_greeting_tweak_dari_registry_bukan_hardcode() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/v2/workflows"
        assert body["flow_id"] == "greeting-flow"
        assert body["mode"] == "sync"
        assert body["session_id"] == "session-1"
        assert "json_input" not in body["tweaks"]
        payload = json.loads(body["tweaks"]["Webhook-Nbrfi"]["data"])
        assert payload["purpose"] == "chat_session_create"
        assert payload["input"]["agent_code"] == "elean"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "has_errors": False,
                "errors": [],
                "output": {
                    "response_text": "Halo! Aku Elean.",
                    "audio_url": "http://minio:9000/media/greet.mp3",
                    "audio_duration_ms": 1200,
                },
            },
        )

    result = await _adapter(respond).run(BINDING, ENVELOPE)
    assert result.response_text == "Halo! Aku Elean."
    assert result.audio_url == "http://minio:9000/media/greet.mp3"
    assert result.audio_duration_ms == 1200


@pytest.mark.asyncio
async def test_greeting_output_text_json_string_diparse() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": {"text": json.dumps({"response_text": "Selamat datang!"})},
            },
        )

    result = await _adapter(respond).run(BINDING, ENVELOPE)
    assert result.response_text == "Selamat datang!"
    assert result.audio_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"status": "failed", "output": {"response_text": "hi"}},
        {"status": "completed", "has_errors": True, "output": {"response_text": "hi"}},
        {"status": "completed", "errors": ["boom"], "output": {"response_text": "hi"}},
        {"status": "completed", "output": {"response_text": "   "}},
        {"status": "completed", "output": "not-a-dict"},
    ],
)
async def test_greeting_response_tidak_valid_ditolak(body) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).run(BINDING, ENVELOPE)
    assert error.value.code == "SESSION_GREETING_UNKNOWN"


@pytest.mark.asyncio
async def test_greeting_http_error_dibungkus() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "bad gateway"})

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).run(BINDING, ENVELOPE)
    assert error.value.code == "SESSION_GREETING_UNKNOWN"


@pytest.mark.asyncio
async def test_greeting_binding_tanpa_input_tweak_component_ditolak() -> None:
    bare_binding = FlowBinding("greeting-flow", "v1", "1", "1", 5000)
    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(lambda request: httpx.Response(200, json={})).run(
            bare_binding, ENVELOPE
        )
    assert error.value.code == "FLOW_NOT_CONFIGURED"
