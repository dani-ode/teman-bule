from __future__ import annotations

import json

import httpx
import pytest

from temanbule.modules.ai_runtime.callcraft import CallCraftClient, CallCraftConfig
from temanbule.platform.errors import DependencyUnavailableError


def _client(handler):
    return CallCraftClient(
        CallCraftConfig(
            base_url="https://callcraft-api.example.test/v1",
            user_id="user",
            public_key="public",
            auth_key="secret",
            project_id="project",
        ),
        httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_execute_sends_trusted_headers_and_arguments() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/call"
        assert request.headers["X-CALL-SPEC-ID"] == "spec-1"
        assert request.headers["Idempotency-Key"] == "key-1"
        assert request.headers["Authorization"] == "Bearer secret"
        assert json.loads(request.content) == {"arguments": {"lemma": "hello"}}
        return httpx.Response(200, json={"status": "succeeded", "result": {"id": "1"}})

    outcome = await _client(handler).execute(
        spec_id="spec-1",
        arguments={"lemma": "hello"},
        request_id="request-1",
        idempotency_key="key-1",
    )
    assert outcome.status_code == 200
    assert outcome.body["status"] == "succeeded"


@pytest.mark.asyncio
async def test_transport_failure_is_unknown_outcome() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(DependencyUnavailableError) as exc_info:
        await _client(handler).execute(
            spec_id="spec-1", arguments={}, request_id="request-1", idempotency_key="key-1"
        )
    assert exc_info.value.code == "CALLCRAFT_OUTCOME_UNKNOWN"


def test_client_rejects_non_https_configuration() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        CallCraftClient(
            CallCraftConfig("http://callcraft.example.test/v1", "u", "p", "a", "project")
        )
