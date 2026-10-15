"""Uji pipeline streaming ChatService: persistensi terkait event SSE.

Fokus kontrak (tanpa DB sungguhan):
- User message disimpan SEBELUM frame pertama Langflow diminta (lazy pull),
  sehingga retry client dengan client_key sama tidak memanggil ulang provider.
- Teks pesan user voice adalah transkrip final dari flow ``stt`` (alur
  3-hit) — tersimpan sejak INSERT; tidak ada UPDATE placeholder.
- Terminal ``completed`` memicu persist agent message dan membawa payload
  identik respons sync (message_id, user_message_id, audio_url, ...).
- Terminal ``failed`` (vendor) diteruskan apa adanya; tidak ada agent message.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from temanbule.modules.conversations.chat import ChatService, _PreparedTurn
from temanbule.modules.conversations.langflow import ChatResult, ChatStreamEvent
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


def _prepared(*, modality: str = "text") -> _PreparedTurn:
    return _PreparedTurn(
        user_id="user-1",
        session_id="session-1",
        binding=SimpleNamespace(),
        envelope={"request_id": "turn-1", "session": {"session_id": "session-1"}},
        practice=SimpleNamespace(agent_version_id="agent-version-1"),
        turn_input={
            "modality": modality,
            "text": "halo" if modality == "text" else "I wanna go to Paris.",
            "media_id": "" if modality == "text" else "media-1",
            "storage_key": "",
        },
        client_key="client-key-1",
    )


class _FakeAdapter:
    """Adapter double: run_stream mengembalikan skenario event yang di-script."""

    scenario: list[ChatStreamEvent] = []
    invoked: int = 0

    def __init__(self, settings: Settings):
        pass

    async def run_stream(self, binding, envelope):
        type(self).invoked += 1
        for event in type(self).scenario:
            yield event


def _result() -> ChatResult:
    return ChatResult(
        schema_version="3", request_id="turn-1", status="completed",
        response_text="Ah, Paris minggu ini?",
        audio_url="https://minio/agent.mp3", audio_duration_ms=4200,
    )


def _service(monkeypatch: pytest.MonkeyPatch) -> ChatService:
    _FakeAdapter.scenario = []
    _FakeAdapter.invoked = 0
    service = ChatService(
        session=AsyncMock(), settings=Settings(_env_file=None),
        adapter=_FakeAdapter(Settings(_env_file=None)),
    )
    conversations = SimpleNamespace(
        append_user_message=AsyncMock(
            return_value=SimpleNamespace(id="user-msg-1", text="I wanna go to Paris.")
        ),
        append_agent_message=AsyncMock(
            return_value=SimpleNamespace(id="agent-msg-1")
        ),
    )
    monkeypatch.setattr(
        "temanbule.modules.conversations.chat.ConversationService",
        lambda *args, **kwargs: conversations,
    )
    monkeypatch.setattr(
        ChatService, "_resolve_media_id_from_result",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ChatService, "_relocate_agent_audio", AsyncMock())
    service._test_conversations = conversations
    return service


@pytest.mark.asyncio
async def test_stream_persists_user_message_before_consuming_provider(monkeypatch):
    """User message tersimpan sebelum frame pertama Langflow ditarik.

    Catatan: event ``started`` adapter diproduksi sebelum koneksi HTTP
    dibuka, sehingga yang diuji di sini adalah persist terjadi setelah
    started dan sebelum event berikutnya (bukan sebelum generator mulai).
    """
    service = _service(monkeypatch)
    order: list[str] = []

    async def append_user_message(**kwargs):
        order.append("persist_user")
        return SimpleNamespace(id="user-msg-1", text="halo")

    service._test_conversations.append_user_message = AsyncMock(
        side_effect=append_user_message
    )

    async def run_stream(binding, envelope):
        order.append("provider_started")
        yield ChatStreamEvent(kind="started", data={"request_id": "turn-1"})
        order.append("next_frame_pulled")
        yield ChatStreamEvent(kind="completed", result=_result())

    service.adapter.run_stream = run_stream

    events = [event async for event in service._run_turn_stream(_prepared())]

    # Persist user terjadi sebelum frame setelah started diminta.
    assert order == ["provider_started", "persist_user", "next_frame_pulled"]
    kinds = [event.kind for event in events]
    assert kinds == ["started", "completed"]
    assert events[0].data["user_message_id"] == "user-msg-1"
    completed = events[-1]
    assert completed.data["message_id"] == "agent-msg-1"
    assert completed.data["user_message_id"] == "user-msg-1"
    assert completed.data["audio_url"] == "https://minio/agent.mp3"
    assert completed.data["response_text"] == "Ah, Paris minggu ini?"


@pytest.mark.asyncio
async def test_stream_failed_terminal_persisted_user_only(monkeypatch):
    service = _service(monkeypatch)
    _FakeAdapter.scenario = [
        ChatStreamEvent(kind="started", data={"request_id": "turn-1"}),
        ChatStreamEvent(kind="failed", error="boom",
                        data={"code": "CHAT_OUTCOME_UNKNOWN"}),
    ]
    events = [event async for event in service._run_turn_stream(_prepared())]

    assert [event.kind for event in events] == ["started", "failed"]
    service._test_conversations.append_user_message.assert_awaited_once()
    service._test_conversations.append_agent_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_voice_message_text_final_sejak_insert(monkeypatch):
    """Alur 3-hit: teks voice message = transkrip final, sejak INSERT.

    STT sudah terjadi via flow ``stt`` sebelum chat turn, sehingga teks
    yang disimpan ke DB langsung berupa transkrip — tidak ada placeholder
    dan tidak ada UPDATE (tabel messages tetap append-only).
    """
    service = _service(monkeypatch)
    user_message = SimpleNamespace(id="user-msg-1", text="I wanna go to Paris.")
    service._test_conversations.append_user_message = AsyncMock(
        return_value=user_message
    )
    _FakeAdapter.scenario = [
        ChatStreamEvent(kind="started", data={"request_id": "turn-1"}),
        ChatStreamEvent(kind="text_delta", text="Ah, Paris minggu ini?"),
        ChatStreamEvent(kind="usage_updated", usage={"input_tokens": 10}),
        ChatStreamEvent(kind="completed", result=_result()),
    ]
    events = [
        event async for event in service._run_turn_stream(_prepared(modality="audio"))
    ]

    assert [event.kind for event in events] == [
        "started", "text_delta", "usage_updated", "completed",
    ]
    # User message disimpan sebagai audio dengan media_id + teks transkripnya.
    kwargs = service._test_conversations.append_user_message.await_args.kwargs
    assert kwargs["modality"] == "audio"
    assert kwargs["media_id"] == "media-1"
    assert kwargs["text"] == "I wanna go to Paris."


@pytest.mark.asyncio
async def test_stream_duplicate_client_key_fails_before_provider_call(monkeypatch):
    """Replay client_key → failed(DUPLICATE_MESSAGE) tanpa started palsu.

    Event ``started`` sengaja TIDAK diteruskan pada kasus duplikat agar
    client tidak mengira turn baru dimulai; cukup ambil history terbaru.
    """
    service = _service(monkeypatch)
    service._test_conversations.append_user_message = AsyncMock(
        side_effect=__import__(
            "temanbule.platform.errors", fromlist=["ConflictError"]
        ).ConflictError("sudah ada")
    )
    _FakeAdapter.scenario = [ChatStreamEvent(kind="started", data={"request_id": "t"})]

    events = [event async for event in service._run_turn_stream(_prepared())]

    assert len(events) == 1
    assert events[0].kind == "failed"
    assert events[0].data["code"] == "DUPLICATE_MESSAGE"
    service._test_conversations.append_agent_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_adapter_not_configured_propagates(monkeypatch):
    service = _service(monkeypatch)

    async def run_stream(binding, envelope):
        raise DependencyUnavailableError("Langflow belum dikonfigurasi.")
        yield  # pragma: no cover

    service.adapter.run_stream = run_stream
    with pytest.raises(DependencyUnavailableError):
        [event async for event in service._run_turn_stream(_prepared())]
