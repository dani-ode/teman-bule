"""Transport doubles validate protocol handling, not live vendor compatibility.

Kontrak: key ``tweaks`` menunjuk komponen canvas yang dipin di registry
(``input_tweak_component``), envelope dikirim sebagai JSON string di field
``data`` komponen Webhook — tidak ada hardcode ``json_input``.
"""

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.conversations.langflow import LangflowChatAdapter
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "correlation", "errors", "text", "http"])
async def test_chat_matches_deployed_v2_schema(failure):
    def respond(request):
        body = json.loads(request.content)
        assert body["flow_id"] == "registered-flow"
        assert body["mode"] == "sync"
        assert "input_type" not in body
        assert "json_input" not in body["tweaks"]
        assert json.loads(body["tweaks"]["Webhook-HxHuc"]["data"])["request_id"] == "turn-1"
        output = {
            "schema_version": "1", "request_id": "turn-1", "status": "completed",
            "response_text": "Hello!", "citations": [], "tool_outcomes": [],
        }
        if failure == "correlation":
            output["request_id"] = "wrong"
        if failure == "text":
            output["response_text"] = " "
        return httpx.Response(503 if failure == "http" else 200, json={
            "status": "completed", "has_errors": False,
            "errors": ["failed"] if failure == "errors" else [], "output": output,
        })

    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"), httpx.MockTransport(respond),
    )
    binding = FlowBinding("registered-flow", "chat.v1", "3", "1", 3000,
                          input_tweak_component="Webhook-HxHuc")
    envelope = {"request_id": "turn-1", "session": {"session_id": "session-1"}}
    if failure:
        with pytest.raises(DependencyUnavailableError) as error:
            await adapter.run(binding, envelope)
        assert error.value.code == "CHAT_OUTCOME_UNKNOWN"
    else:
        assert (await adapter.run(binding, envelope)).response_text == "Hello!"


@pytest.mark.asyncio
async def test_chat_binding_tanpa_input_tweak_component_ditolak():
    """Komponen tweak wajib dipin di registry; tanpa pin → FLOW_NOT_CONFIGURED."""
    adapter = LangflowChatAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    binding = FlowBinding("registered-flow", "chat.v1", "3", "1", 3000)
    envelope = {"request_id": "turn-1", "session": {"session_id": "session-1"}}
    with pytest.raises(DependencyUnavailableError) as error:
        await adapter.run(binding, envelope)
    assert error.value.code == "FLOW_NOT_CONFIGURED"
