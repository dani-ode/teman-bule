"""Uji pipeline streaming create-session ChatService: greeting via SSE.

Fokus kontrak (tanpa DB sungguhan):
- Session dibuat dulu (start_practice_session) sebelum stream dibuka, sehingga
  room tetap ada apa pun hasil greeting-nya.
- Terminal ``completed`` memicu persist greeting sebagai agent message dan
  membawa payload identik respons sync (first_message, audio_url, ...).
- Terminal ``failed`` (vendor) diteruskan dengan first_message=None — session
  tetap ter-commit oleh router.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from temanbule.modules.conversations.chat import ChatService
from temanbule.modules.conversations.langflow import ChatStreamEvent
from temanbule.modules.conversations.session_create import SessionCreateResult
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class _FakeSessionCreateAdapter:
    """Adapter double: run_stream mengembalikan skenario event yang di-script."""

    scenario: list[ChatStreamEvent] = []

    def __init__(self, settings: Settings):
        pass

    async def run_stream(self, binding, envelope):
        for event in type(self).scenario:
            yield event


class _FakeNested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


def _greeting_result() -> SessionCreateResult:
    return SessionCreateResult(
        response_text="Halo! Aku Elean.",
        audio_url="http://minio:9000/media/greet.mp3",
        audio_duration_ms=1200,
    )


def _service(monkeypatch: pytest.MonkeyPatch) -> ChatService:
    _FakeSessionCreateAdapter.scenario = []
    fake_session = AsyncMock()
    fake_session.begin_nested = lambda: _FakeNested()
    service = ChatService(
        session=fake_session, settings=Settings(_env_file=None),
        session_create_adapter=_FakeSessionCreateAdapter(Settings(_env_file=None)),
    )
    conversation = SimpleNamespace(
        id="session-1", kind="chat", state="active",
        runtime_snapshot_id="snap-1",
        started_at=__import__("datetime").datetime(
            2024, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
    )
    conversations = SimpleNamespace(
        start_practice_session=AsyncMock(return_value=conversation),
        get_practice_session=AsyncMock(
            return_value=SimpleNamespace(agent_version_id="agent-version-1")
        ),
        append_agent_message=AsyncMock(return_value=SimpleNamespace(
            id="agent-msg-1", session_id="session-1", role="agent",
            text="Halo! Aku Elean.", modality="audio", media_id=None,
            sequence=1, terminal_state="ready",
            created_at=conversation.started_at,
        )),
    )
    monkeypatch.setattr(
        "temanbule.modules.conversations.chat.ConversationService",
        lambda *args, **kwargs: conversations,
    )
    monkeypatch.setattr(
        "temanbule.modules.conversations.chat.resolve_flow",
        AsyncMock(return_value=SimpleNamespace(input_schema_version="1")),
    )
    monkeypatch.setattr(
        "temanbule.modules.conversations.chat.chat_context",
        AsyncMock(return_value={
            "persona": {}, "ai_configuration": {}, "agent_code": "elean",
        }),
    )
    monkeypatch.setattr(
        ChatService, "_resolve_media_id_from_result", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ChatService, "_relocate_agent_audio", AsyncMock())
    service._test_conversations = conversations
    return service


@pytest.mark.asyncio
async def test_greeting_stream_completed_persist_agent_message(monkeypatch):
    service = _service(monkeypatch)
    _FakeSessionCreateAdapter.scenario = [
        ChatStreamEvent(kind="started", data={"request_id": "greet-1"}),
        ChatStreamEvent(kind="text_delta", text="Halo! Aku Elean."),
        ChatStreamEvent(kind="usage_updated", usage={"input_tokens": 320}),
        ChatStreamEvent(kind="completed", result=_greeting_result()),
    ]

    events = [
        event async for event in service.create_session_with_greeting_stream(
            user_id="user-1", agent_code="elean", category_id="cat-1",
        )
    ]

    assert [event.kind for event in events] == [
        "started", "text_delta", "usage_updated", "completed",
    ]
    service._test_conversations.start_practice_session.assert_awaited_once()
    service._test_conversations.append_agent_message.assert_awaited_once()
    kwargs = service._test_conversations.append_agent_message.await_args.kwargs
    assert kwargs["session_id"] == "session-1"
    assert kwargs["text"] == "Halo! Aku Elean."
    assert kwargs["modality"] == "audio"
    assert kwargs["audio_duration_ms"] == 1200

    completed = events[-1]
    assert completed.data["session_id"] == "session-1"
    assert completed.data["agent_code"] == "elean"
    first_message = completed.data["first_message"]
    assert first_message["message_id"] == "agent-msg-1"
    assert first_message["text"] == "Halo! Aku Elean."
    assert first_message["audio_url"] == "http://minio:9000/media/greet.mp3"


@pytest.mark.asyncio
async def test_greeting_stream_failed_session_kept_tanpa_agent_message(monkeypatch):
    service = _service(monkeypatch)
    _FakeSessionCreateAdapter.scenario = [
        ChatStreamEvent(kind="started", data={"request_id": "greet-1"}),
        ChatStreamEvent(
            kind="failed", error="boom",
            data={"code": "SESSION_GREETING_UNKNOWN"},
        ),
    ]

    events = [
        event async for event in service.create_session_with_greeting_stream(
            user_id="user-1", agent_code="elean", category_id="cat-1",
        )
    ]

    assert [event.kind for event in events] == ["started", "failed"]
    service._test_conversations.start_practice_session.assert_awaited_once()
    service._test_conversations.append_agent_message.assert_not_awaited()
    terminal = events[-1]
    assert terminal.data["code"] == "SESSION_GREETING_UNKNOWN"
    assert terminal.data["session_id"] == "session-1"
    assert terminal.data["first_message"] is None


@pytest.mark.asyncio
async def test_greeting_stream_app_error_sebelum_stream_failed_event(monkeypatch):
    """Flow belum terdaftar / AI nonaktif → terminal failed, session tetap ada."""
    service = _service(monkeypatch)
    monkeypatch.setattr(
        "temanbule.modules.conversations.chat.resolve_flow",
        AsyncMock(side_effect=DependencyUnavailableError(
            "belum terdaftar", code="FLOW_NOT_CONFIGURED",
        )),
    )

    events = [
        event async for event in service.create_session_with_greeting_stream(
            user_id="user-1", agent_code="elean", category_id="cat-1",
        )
    ]

    assert len(events) == 1
    assert events[0].kind == "failed"
    assert events[0].data["code"] == "FLOW_NOT_CONFIGURED"
    assert events[0].data["session_id"] == "session-1"
    assert events[0].data["first_message"] is None
    service._test_conversations.start_practice_session.assert_awaited_once()
    service._test_conversations.append_agent_message.assert_not_awaited()
