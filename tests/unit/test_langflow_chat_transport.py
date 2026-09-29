"""Transport doubles validate protocol handling, not live vendor compatibility."""

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
        assert json.loads(body["tweaks"]["json_input"]["payload"])["request_id"] == "turn-1"
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
    binding = FlowBinding("registered-flow", "chat.v1", "3", "1", 3000)
    envelope = {"request_id": "turn-1", "session": {"session_id": "session-1"}}
    if failure:
        with pytest.raises(DependencyUnavailableError) as error:
            await adapter.run(binding, envelope)
        assert error.value.code == "CHAT_OUTCOME_UNKNOWN"
    else:
        assert (await adapter.run(binding, envelope)).response_text == "Hello!"
