"""Transport double untuk flow STT (purpose ``stt``), bukan live vendor.

Kontrak identik adapter chat lain: key ``tweaks`` menunjuk komponen canvas
yang dipin di registry (``input_tweak_component``), envelope dikirim sebagai
JSON string di field ``data`` komponen Webhook. Output akhir wajib berisi
``request_id`` yang berkorelasi + ``text`` transkrip non-kosong.
"""

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.media.stt import LangflowSttAdapter
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


def _binding() -> FlowBinding:
    return FlowBinding("stt-flow", "stt.v1", "1", "1", 30000,
                       input_tweak_component="Webhook-dMtjQ")


def _envelope() -> dict:
    return {
        "schema_version": "1",
        "request_id": "stt-1",
        "purpose": "stt",
        "user": {"user_id": "user-1"},
        "input": {"media_id": "media-1", "storage_key": "media/user-1/key-1"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "correlation", "errors", "text", "http"])
async def test_stt_matches_deployed_v2_schema(failure):
    def respond(request):
        body = json.loads(request.content)
        assert body["flow_id"] == "stt-flow"
        assert body["mode"] == "sync"
        assert "input_type" not in body
        assert "json_input" not in body["tweaks"]
        tweaks = json.loads(body["tweaks"]["Webhook-dMtjQ"]["data"])
        assert tweaks["request_id"] == "stt-1"
        assert tweaks["purpose"] == "stt"
        assert tweaks["input"]["media_id"] == "media-1"
        output = {"request_id": "stt-1", "text": "I wanna go to Paris."}
        if failure == "correlation":
            output["request_id"] = "wrong"
        if failure == "text":
            output["text"] = " "
        return httpx.Response(503 if failure == "http" else 200, json={
            "status": "completed", "has_errors": False,
            "errors": ["failed"] if failure == "errors" else [], "output": output,
        })

    adapter = LangflowSttAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"), httpx.MockTransport(respond),
    )
    if failure:
        with pytest.raises(DependencyUnavailableError) as error:
            await adapter.run(_binding(), _envelope())
        assert error.value.code == "STT_OUTCOME_UNKNOWN"
    else:
        assert (await adapter.run(_binding(), _envelope())).text == "I wanna go to Paris."


@pytest.mark.asyncio
async def test_stt_output_json_string_dalam_fence_diterima():
    """ChatOutput mengembalikan JSON sebagai string berfence — tetap di-parse."""
    def respond(request):
        return httpx.Response(200, json={
            "status": "completed", "has_errors": False, "errors": [],
            "output": "```json\n{\"request_id\": \"stt-1\", \"text\": \"halo\"}\n```",
        })

    adapter = LangflowSttAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"), httpx.MockTransport(respond),
    )
    assert (await adapter.run(_binding(), _envelope())).text == "halo"


@pytest.mark.asyncio
async def test_stt_binding_tanpa_input_tweak_component_ditolak():
    """Komponen tweak wajib dipin di registry; tanpa pin → FLOW_NOT_CONFIGURED."""
    adapter = LangflowSttAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    binding = FlowBinding("stt-flow", "stt.v1", "1", "1", 30000)
    with pytest.raises(DependencyUnavailableError) as error:
        await adapter.run(binding, _envelope())
    assert error.value.code == "FLOW_NOT_CONFIGURED"
