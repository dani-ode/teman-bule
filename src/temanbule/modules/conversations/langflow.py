"""Strict Workflow v2 transport for a structured chat turn.

Komponen canvas target ``tweaks`` tidak di-hardcode — diambil dari registry
(``FlowBinding.input_tweak_component``, kolom ``ai_flow_registry.input_tweak_component``)
agar per-environment dapat dipin tanpa mengubah kode.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

if TYPE_CHECKING:
    from temanbule.modules.conversations.session_create import SessionCreateResult

logger = logging.getLogger(__name__)


def parse_workflow_output(output: Any) -> Any:
    """Bongkar output workflow menjadi dict.

    ChatOutput Langflow selalu mengembalikan JSON sebagai *string* dan sering
    membungkusnya dalam markdown fence ```json ... ```. Backend yang memformat
    ulang ke dict sebelum divalidasi model Pydantic.

    Toleransi format flow: ``citations``/``tool_outcomes`` boleh berupa JSON
    string (mis. ``"[]"``) — dinormalisasi ke list; ``status: "complete"``
    dipetakan ke ``"completed"``.
    """
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        # Wrapper ChatOutput ({"text": ...}) dibongkar; dict yang membawa
        # request_id di level yang sama adalah hasil akhir flow yang sah
        # (mis. kontrak STT {"request_id", "text"}) — jangan dibongkar.
        if not isinstance(output.get("request_id"), str):
            output = output["text"]
    if isinstance(output, str):
        text = output.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        output = json.loads(text)
    if isinstance(output, dict):
        for key in ("citations", "tool_outcomes"):
            if isinstance(output.get(key), str):
                output[key] = json.loads(output[key])
        if isinstance(output.get("schema_version"), int):
            output["schema_version"] = str(output["schema_version"])
        if output.get("status") == "complete":
            output["status"] = "completed"
    return output


class ChatResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # schema_version di-echo balik oleh flow dari envelope input (versi input
    # registry), bukan versi kontrak output — cukup divalidasi sebagai string.
    schema_version: str = Field(min_length=1, max_length=40)
    request_id: str
    status: Literal["completed"]
    response_text: str = Field(min_length=1, max_length=32000)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tool_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # Flat fields (bukan nested object) konsisten dengan SessionCreateResult
    audio_url: str | None = Field(default=None, max_length=2048)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    object_key: str | None = Field(default=None, max_length=512)
    bucket: str | None = Field(default=None, max_length=128)
    background_dispatch_ref: str | None = None
    # Teks asli pesan user hasil STT (hanya ada pada turn voice; greeting dan
    # turn teks tidak mengembalikan field ini). Dipakai backend untuk menyimpan
    # transcript permanen menggantikan placeholder "[voice message]".
    user_message: str | None = Field(default=None, max_length=32000)


# --- Streaming (Workflow API mode="stream") ---------------------------------


def _is_envelope_echo(text: str) -> bool:
    """True bila ``text`` adalah echo envelope input, bukan output akhir flow.

    Komponen TextOutput pada flow TemanBule meng-echo balik envelope input
    (field ``purpose`` + ``input``/``session``), sehingga tidak boleh dianggap
    sebagai output akhir. Output akhir yang sah (ChatOutput) berupa JSON TTS
    (``response_text``/``provider``) atau teks tutor polos — tidak memuat
    ``purpose`` di level atas.
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return False
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    candidate = fence.group(1).strip() if fence else stripped
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, dict) and "purpose" in parsed


# Vertex type komponen canvas yang relevan dengan kontrak SSE aplikasi.
# Diturunkan dari field output_meta.vertex_type / vertex id pada event
# end_vertex Langflow — bukan dari display name agar tahan rename di canvas.
_LLM_VERTEX_TYPE = "LanguageModelComponent"
_STT_VERTEX_TYPE = "SpeechToText"

# Frame langkah per komponen (add_message/log/end_vertex) sangat banyak;
# dibatasi agar konsumen tidak memproses noise ratusan frame per turn.
_STREAM_FRAME_LIMIT = 2000


@dataclass(slots=True)
class ChatStreamEvent:
    """Event SSE aplikasi hasil mapping dari frame EventManager Langflow.

    Jenis event mengikuti kontrak api-events.md: ``started`` → nol/lebih
    ``transcript``/``text_delta``/``usage_updated`` → tepat satu terminal
    (``completed``/``failed``). Terminal ``cancelled`` belum dipakai —
    pembatalan client saat ini menghentikan konsumsi stream tanpa
    menghentikan invocation Langflow.

    ``data`` membawa payload tambahan per jenis event (mis. ``request_id``
    dan ``session_id`` pada ``started``, hasil persistensi pada
    ``completed``) tanpa memperluas dataclass setiap kali kontrak bertambah.
    """

    kind: Literal[
        "started", "transcript", "text_delta", "usage_updated", "completed", "failed"
    ]
    text: str | None = None
    usage: dict[str, int] | None = None
    # ChatResult untuk chat turn; SessionCreateResult untuk greeting
    # (session_create mengimpor modul ini, jadi tipenya forward-reference).
    result: "ChatResult | SessionCreateResult | None" = None
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _StreamState:
    """Akumulator stateful saat mengonsumsi frame SSE Langflow."""

    request_id: str
    text_delta_sent: bool = False
    # Teks respons yang sudah dikirim sebagai text_delta; dipakai untuk
    # merekonstruksi hasil terminal bila output akhir flow tidak tersedia.
    response_text: str | None = None
    usage: dict[str, int] | None = None
    final_output: Any = None
    # Output per-komponen dari event ``output`` (component_id -> teks content).
    # ChatOutput tidak meng-emisi teks di end_vertex; payload-nya hanya ada di
    # event ``output``. TextOutput meng-emisi echo envelope yang harus ditolak,
    # sehingga penangkapan dilakukan per-komponen lalu diprioritaskan.
    component_outputs: dict[str, str] = field(default_factory=dict)
    events: list[ChatStreamEvent] = field(default_factory=list)


def _extract_raw_text(build_data: dict[str, Any]) -> str | None:
    """Ambil teks mentah pertama dari artifacts output sebuah end_vertex."""
    data = build_data.get("data") if isinstance(build_data, dict) else None
    if not isinstance(data, dict):
        return None
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        return None
    for artifact in artifacts.values():
        if isinstance(artifact, dict) and isinstance(artifact.get("raw"), str):
            raw: str = artifact["raw"]
            stripped = raw.strip()
            if stripped:
                return stripped
    return None


def _extract_message_text(build_data: dict[str, Any]) -> str | None:
    """Ambil teks dari build_data.message.message untuk ChatOutput.

    ChatOutput di Langflow stream tidak meng-emisi artifact.raw, tapi menyimpan
    payload di build_data.message.message (dict dengan key 'message').
    """
    if not isinstance(build_data, dict):
        return None
    message = build_data.get("message")
    if not isinstance(message, dict):
        return None
    # Cek key 'message' (struktur ChatOutput)
    text = message.get("message")
    if isinstance(text, str) and text.strip():
        return text.strip()
    # Cek key 'text' (struktur alternatif)
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _extract_usage(build: dict[str, Any]) -> dict[str, int] | None:
    """Ambil token usage dari artifacts/token_usage sebuah end_vertex."""
    artifacts = build.get("data", {}).get("artifacts", {})
    if isinstance(artifacts, dict):
        for artifact in artifacts.values():
            if isinstance(artifact, dict) and isinstance(artifact.get("usage"), dict):
                usage = {
                    str(key): int(value)
                    for key, value in artifact["usage"].items()
                    if isinstance(value, int | float)
                }
                return usage or None
    raw_usage = build.get("token_usage")
    if isinstance(raw_usage, dict):
        usage = {
            str(key): int(value)
            for key, value in raw_usage.items()
            if isinstance(value, int | float)
        }
        return usage or None
    return None


def _map_end_vertex(frame_data: dict[str, Any], state: _StreamState) -> ChatStreamEvent | None:
    """Petakan satu event end_vertex Langflow ke event aplikasi (bila relevan).

    Pemetaan mengikuti vertex_type komponen canvas:

    - ``SpeechToText`` → ``transcript``: teks hasil STT tersedia jauh sebelum
      LLM selesai, sehingga klien voice message dapat menampilkan transkrip
      lebih awal.
    - ``LanguageModelComponent`` → ``text_delta``: respons teks final model.
      Flow chat TemanBule merakit JSON output di komponen downstream
      (CombineJSON), sehingga raw output LLM sudah berupa teks tutor bersih
      yang dapat langsung ditampilkan. Dikirim sebagai SATU delta karena
      event ``token`` tidak tersedia pada deployment saat ini; bila nanti
      token streaming aktif, handler token di run_stream() yang lebih dulu
      mengirim delta dan event ini diabaikan (dedupe via state).
    - ``ChatOutput``/``TextOutput`` → output final flow disimpan di state;
      caller membongkarnya ke ChatResult saat frame ``end``.
    """
    build = frame_data.get("build_data")
    if not isinstance(build, dict):
        return None
    meta = frame_data.get("output_meta")
    vertex_type = meta.get("vertex_type") if isinstance(meta, dict) else None

    if vertex_type == _STT_VERTEX_TYPE:
        transcript = _extract_raw_text(build)
        if transcript:
            return ChatStreamEvent(kind="transcript", text=transcript)
        return None

    if vertex_type == _LLM_VERTEX_TYPE:
        # Usage dari provider adapter (bukan angka tulisan LLM) — api-events.md.
        usage = _extract_usage(build)
        if usage:
            state.usage = usage
        text = _extract_raw_text(build)
        events_out: list[ChatStreamEvent] = []
        if text and not state.text_delta_sent:
            state.text_delta_sent = True
            state.response_text = text
            events_out.append(ChatStreamEvent(kind="text_delta", text=text))
        if usage:
            events_out.append(ChatStreamEvent(kind="usage_updated", usage=usage))
        # Kembalikan event pertama; sisa diantre via state.events dan
        # dikeluarkan run_stream() sebelum memproses frame berikutnya.
        if len(events_out) > 1:
            state.events.extend(events_out[1:])
        return events_out[0] if events_out else None

    if vertex_type in ("ChatOutput", "TextOutput") and isinstance(meta, dict):
        if meta.get("is_terminal"):
            # Prioritaskan ChatOutput daripada TextOutput:
            # - ChatOutput: selalu terima (overwrite final_output)
            # - TextOutput: hanya terima jika final_output belum ada
            should_capture = (
                vertex_type == "ChatOutput"
                or (vertex_type == "TextOutput" and state.final_output is None)
            )
            if should_capture:
                # Coba artifacts.raw dulu (standar), lalu fallback ke message.message
                text = _extract_raw_text(build)
                if not text:
                    text = _extract_message_text(build)
                # Tolak echo envelope (mis. TextOutput yang meng-echo input).
                if text and _is_envelope_echo(text):
                    logger.debug("rejected envelope echo from %s end_vertex", vertex_type)
                    text = None
                if text:
                    state.final_output = text
    return None


class LangflowChatAdapter:
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

    async def run(self, binding: FlowBinding, envelope: dict[str, Any]) -> ChatResult:
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
                    "chat workflow tidak completed: %r",
                    str(body.get("errors") if isinstance(body, dict) else body)[:600],
                )
                raise ValueError("Workflow not completed")
            if body.get("has_errors", False) is not False or body.get("errors"):
                raise ValueError("Workflow errors")
            output = parse_workflow_output(body.get("output"))
            result = ChatResult.model_validate(output)
            if result.request_id != envelope["request_id"] or not result.response_text.strip():
                raise ValueError("Correlation or text invalid")
            return result
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("chat workflow gagal: %r", exc, exc_info=False)
            raise DependencyUnavailableError(
                "Hasil workflow chat belum dapat dikonfirmasi.", code="CHAT_OUTCOME_UNKNOWN",
            ) from exc

    async def run_stream(
        self, binding: FlowBinding, envelope: dict[str, Any]
    ) -> AsyncIterator[ChatStreamEvent]:
        """Jalankan chat turn via Workflow API ``mode="stream"`` (SSE).

        Menghasilkan event aplikasi bertahap: ``started`` → ``transcript``
        (voice, dari end_vertex STT) → ``text_delta`` (dari end_vertex LLM;
        SATU delta penuh selama event token belum tersedia di deployment)
        → ``usage_updated`` → tepat satu terminal ``completed``/``failed``.

        Output akhir tetap divalidasi persis seperti mode sync
        (parse_workflow_output + ChatResult + korelasi request_id), sehingga
        kontrak hasil identik — hanya waktu tampilnya yang lebih awal.
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
                            self._capture_output_frame(frame_data, state)
                        elif event_name == "end":
                            # Frame terminal flow: bawa status + output akhir.
                            self._capture_end_frame(frame_data, state)
            if terminal is None:
                terminal = self._terminal_from_state(state, envelope)
        except DependencyUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("chat workflow stream gagal: %r", exc, exc_info=False)
            terminal = ChatStreamEvent(
                kind="failed",
                error="Hasil workflow chat belum dapat dikonfirmasi.",
                data={"code": "CHAT_OUTCOME_UNKNOWN"},
            )
        yield terminal

    @staticmethod
    def _capture_output_frame(frame_data: dict[str, Any], state: _StreamState) -> None:
        """Kumpulkan output dari frame ``output`` (EventManager stream).

        ChatOutput tidak meng-emisi teks di ``end_vertex`` — payload-nya hanya
        tersedia di event ``output``. Event ini datang SATU KALI PER komponen
        output (bukan map gabungan), membawa ``component_id`` + ``content``.
        Karena itu output diakumulasi per-komponen di ``state.component_outputs``
        lalu dipilih di :meth:`_select_final_output` saat frame ``end``:

        - Prioritas ``ChatOutput`` (payload TTS/hasil akhir yang sebenarnya).
        - ``TextOutput`` ditolak bila berisi echo envelope (input yang di-echo).

        Frame ``output`` juga bisa berbentuk map gabungan ``{<id>: {...}}``
        (deployment tertentu); keduanya didukung.
        """
        def _content_text(node: Any) -> str | None:
            if not isinstance(node, dict):
                return None
            content = node.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, dict):
                inner = content.get("text") or content.get("message")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
            return None

        # Bentuk per-komponen: {"component_id": ..., "content": ..., ...}
        component_id = frame_data.get("component_id")
        if isinstance(component_id, str):
            text = _content_text(frame_data)
            if text:
                state.component_outputs[component_id] = text
            return

        # Bentuk map gabungan: {"ChatOutput-x": {"content": ...}, ...}
        for key, node in frame_data.items():
            if isinstance(node, dict):
                text = _content_text(node)
                if text:
                    state.component_outputs[str(key)] = text

    @staticmethod
    def _select_final_output(state: _StreamState) -> None:
        """Pilih output final dari ``component_outputs`` (prioritas ChatOutput).

        Dipanggil saat frame ``end`` (atau saat terminal dibangun) bila
        ``final_output`` belum terisi dari end_vertex. Echo envelope ditolak.
        """
        if state.final_output is not None:
            return
        if not state.component_outputs:
            return
        ordered = sorted(
            state.component_outputs.items(),
            key=lambda kv: 0 if "ChatOutput" in kv[0] else 1,
        )
        for component_id, text in ordered:
            if _is_envelope_echo(text):
                logger.debug("rejected envelope echo from output %s", component_id)
                continue
            state.final_output = text
            logger.debug("final_output selected from %s", component_id)
            return

    @staticmethod
    def _capture_end_frame(frame_data: dict[str, Any], state: _StreamState) -> None:
        """Ambil output akhir dari frame ``end`` bila terminal vertex belum ada."""
        # Frame ``end`` adalah titik terminal: pilih dari component_outputs
        # yang terkumpul dari event ``output`` (sumber kebenaran ChatOutput).
        LangflowChatAdapter._select_final_output(state)
        if state.final_output is not None:
            return
        # Frame "end" bisa membawa "output" sebagai:
        # - objek {"text": ...} (output yang dinormalisasi Langflow)
        # - dict hasil akhir flow lengkap (schema_version/request_id/...)
        # - string JSON/teks langsung
        # Normalisasi ke bentuk yang dapat di-parse _terminal_from_state.
        output = frame_data.get("output")
        if isinstance(output, dict):
            # {"text": ...} → ambil text; selain itu simpan dict apa adanya
            # (bisa berupa hasil akhir lengkap yang lolos validasi).
            if isinstance(output.get("text"), str) and output["text"].strip():
                candidate = output["text"].strip()
                if not _is_envelope_echo(candidate):
                    state.final_output = candidate
            elif "purpose" not in output:
                # Dict hasil akhir (bukan echo envelope) — simpan utuh.
                state.final_output = output
            return
        if isinstance(output, str) and output.strip():
            candidate = output.strip()
            if not _is_envelope_echo(candidate):
                state.final_output = candidate

    @staticmethod
    def _terminal_from_state(
        state: _StreamState, envelope: dict[str, Any]
    ) -> ChatStreamEvent:
        """Bangun terminal event dari output final yang terkumpul.

        Jalur utama: parse ``state.final_output`` (JSON hasil akhir flow) ke
        ``ChatResult`` — identik mode sync. Sebagian deployment tidak
        meng-emisi JSON hasil akhir ke stream (LLM/CombineJSON berada di
        sub-flow, frame ``end`` hanya membawa ``build_duration``); dalam
        kasus itu output terminal flow bisa berupa template echo envelope
        yang tidak lolos validasi. Bila demikian, rekonstruksi hasil minimal
        dari ``state.response_text`` (teks yang sudah dikirim sebagai
        ``text_delta``) — ``audio_url`` menjadi ``None`` (fallback text-only
        yang memang didukung kontrak api-events.md).

        Aturan ketat: output akhir flow WAJIB valid (parse + ChatResult +
        korelasi request_id + response_text non-kosong). Bila tidak ada /
        invalid / salah korelasi, turn dianggap GAGAL — tidak ada fallback
        rekonstruksi dari response_text LLM, karena itu menoleransi output
        flow yang rusak.
        """
        # Pengaman: bila frame ``end`` tidak ter-proses (mis. SSE terputus),
        # tetap pilih output final dari component_outputs yang terkumpul.
        LangflowChatAdapter._select_final_output(state)
        if state.final_output is None:
            raise ValueError("No final output in stream state")
        output = parse_workflow_output(state.final_output)
        candidate = ChatResult.model_validate(output)
        if candidate.request_id != envelope["request_id"]:
            raise ValueError("Output correlation invalid")
        if not candidate.response_text.strip():
            raise ValueError("Output response_text blank")
        return ChatStreamEvent(kind="completed", result=candidate)
