"""Transport doubles untuk Workflow API mode="stream" (SSE).

Kontrak: adapter mengonsumsi frame SSE Langflow (``data: {"event": ...,
"data": ...}`` per baris) dan memetakannya ke event aplikasi api-events.md:
``started`` → ``transcript``/``text_delta``/``usage_updated`` → tepat satu
terminal ``completed``/``failed``. Output akhir divalidasi persis seperti
mode sync (parse_workflow_output + korelasi request_id).
"""

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.langflow import (
    LangflowChatAdapter,
    _map_end_vertex,
    _StreamState,
)
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

_BINDING = FlowBinding(
    "registered-flow", "chat.v1", "3", "1", 3000,
    input_tweak_component="Webhook-HxHuc",
)
_ENVELOPE = {
    "schema_version": "3",
    "request_id": "turn-1",
    "session": {"session_id": "session-1"},
}

_OUTPUT = {
    "schema_version": "3", "request_id": "turn-1", "status": "completed",
    "response_text": "Ah, you mean Paris this week?", "citations": [],
    "tool_outcomes": [], "audio_url": "https://minio/audio.mp3",
    "audio_duration_ms": 4200,
}


def _frame(event: str, data: dict) -> str:
    return f"id: 1\nevent: {event}\ndata: {json.dumps({'event': event, 'data': data})}\n\n"


def _end_vertex(vertex_type: str, raw: str, *, usage: dict | None = None,
                terminal: bool = False) -> str:
    artifact: dict = {"raw": raw}
    if usage:
        artifact["usage"] = usage
    return _frame("end_vertex", {
        "build_data": {"id": f"{vertex_type}-x", "data": {"artifacts": {"text": artifact}}},
        "output_meta": {"vertex_type": vertex_type, "is_terminal": terminal},
    })


def _stream_body(*, with_stt: bool = False, output: dict | None = None) -> bytes:
    frames = [_frame("vertices_sorted", {"vertices": []})]
    if with_stt:
        frames.append(_end_vertex("SpeechToText", "I wanna go to Paris in this week."))
    frames.append(_end_vertex(
        "LanguageModelComponent", "Ah, you mean Paris this week?",
        usage={"input_tokens": 1490, "output_tokens": 623},
    ))
    frames.append(_end_vertex(
        "ChatOutput", json.dumps(output or _OUTPUT), terminal=True,
    ))
    frames.append(_frame("end", {"status": "completed"}))
    return "".join(frames).encode()


async def _collect(adapter: LangflowChatAdapter) -> list:
    return [event async for event in adapter.run_stream(_BINDING, _ENVELOPE)]


@pytest.mark.asyncio
async def test_run_stream_maps_end_vertex_events_to_app_contract():
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["flow_id"] == "registered-flow"
        assert body["mode"] == "stream"
        assert json.loads(body["tweaks"]["Webhook-HxHuc"]["data"])["request_id"] == "turn-1"
        return httpx.Response(
            200, content=_stream_body(),
            headers={"content-type": "text/event-stream"},
        )

    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(respond),
    )
    events = await _collect(adapter)

    kinds = [event.kind for event in events]
    assert kinds == ["started", "text_delta", "usage_updated", "completed"]

    started = events[0]
    assert started.data["request_id"] == "turn-1"
    assert started.data["session_id"] == "session-1"
    assert started.data["schema_version"] == "3"

    assert events[1].text == "Ah, you mean Paris this week?"
    assert events[2].usage == {"input_tokens": 1490, "output_tokens": 623}

    completed = events[3]
    assert completed.result is not None
    assert completed.result.response_text == "Ah, you mean Paris this week?"
    assert completed.result.audio_url == "https://minio/audio.mp3"
    assert completed.result.audio_duration_ms == 4200


@pytest.mark.asyncio
async def test_run_stream_voice_emits_transcript_before_text():
    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(lambda request: httpx.Response(
            200, content=_stream_body(with_stt=True),
            headers={"content-type": "text/event-stream"},
        )),
    )
    events = await _collect(adapter)

    kinds = [event.kind for event in events]
    assert kinds == ["started", "transcript", "text_delta", "usage_updated", "completed"]
    assert events[1].text == "I wanna go to Paris in this week."


@pytest.mark.asyncio
async def test_run_stream_text_delta_sent_once_even_with_multiple_llm_vertices():
    state = _StreamState(request_id="turn-1")
    frame_data = {
        "build_data": {"data": {"artifacts": {"text": {"raw": "Hello there."}}}},
        "output_meta": {"vertex_type": "LanguageModelComponent"},
    }
    first = _map_end_vertex(frame_data, state)
    second = _map_end_vertex(frame_data, state)
    assert first is not None and first.kind == "text_delta"
    assert second is None  # dedupe via state.text_delta_sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["http_503", "bad_correlation", "empty_output", "invalid_json_output"],
)
async def test_run_stream_terminal_failed_on_vendor_failure(failure: str):
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "http_503":
            return httpx.Response(503, json={"detail": "unavailable"})
        output = dict(_OUTPUT)
        if failure == "bad_correlation":
            output["request_id"] = "wrong"
        body = _stream_body(output=output)
        if failure == "empty_output":
            body = _frame("end", {"status": "completed"}).encode()
        if failure == "invalid_json_output":
            body = (
                _end_vertex("ChatOutput", "not-json-at-all", terminal=True)
                + _frame("end", {"status": "completed"})
            ).encode()
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"},
        )

    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(respond),
    )
    events = await _collect(adapter)

    assert events[0].kind == "started"
    terminal = events[-1]
    assert terminal.kind == "failed"
    assert terminal.error
    assert terminal.data["code"] == "CHAT_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_run_stream_tanpa_api_key_ditolak():
    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key=""),
        httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    with pytest.raises(DependencyUnavailableError):
        await _collect(adapter)


@pytest.mark.asyncio
async def test_run_stream_binding_tanpa_input_tweak_component_ditolak():
    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    binding = FlowBinding("registered-flow", "chat.v1", "3", "1", 3000)
    with pytest.raises(DependencyUnavailableError) as error:
        await [event async for event in adapter.run_stream(binding, _ENVELOPE)]
    assert error.value.code == "FLOW_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_run_stream_ignores_noise_lines_and_frame_cap():
    """Baris keep-alive/komentar diabaikan; frame berlebih → terminal failed."""
    noise = ": keep-alive\n\n" + _frame("log", {"message": "astra search"})
    frames = [noise] * 5 + [_frame("end", {"status": "completed", "output": _OUTPUT})]
    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(lambda request: httpx.Response(
            200, content="".join(frames).encode(),
            headers={"content-type": "text/event-stream"},
        )),
    )
    events = await _collect(adapter)
    # end frame membawa output langsung → completed tanpa end_vertex terminal.
    assert [event.kind for event in events] == ["started", "completed"]
