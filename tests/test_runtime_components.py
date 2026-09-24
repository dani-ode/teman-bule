"""Exercise the runtime trust boundary without provider calls or Langflow."""

import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from custom_langflow_components.teman_bule_runtime import (
    RuntimeBoundaryError,
    RuntimeClient,
    RuntimeRequest,
    RuntimeSettings,
    ToolRequest,
)

ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OTHER_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def settings() -> RuntimeSettings:
    return RuntimeSettings(
        base_url="https://runtime.example.test",
        service_token=SecretStr("test-service-credential"),
        context_path="/internal/context",
        tool_path="/internal/tools",
        timeout_seconds=1.0,
        max_response_bytes=4096,
    )


def request() -> RuntimeRequest:
    return RuntimeRequest(schema_version="1", request_id=ID, execution_ref=ID)


def context() -> dict[str, object]:
    return {
        "schema_version": "1", "request_id": ID, "execution_ref": ID,
        "runtime_snapshot_id": ID, "purpose": "practice_interaction", "plan": "advance",
        "capability": "llm", "payer": "user_byok", "model_configuration_id": ID,
        "allowed_tools": ["vocabulary.save"],
    }


@pytest.mark.asyncio
async def test_context_rejects_secret_leak_and_mismatched_grant() -> None:
    for mutation in ({"api_key": "private-value"}, {"execution_ref": OTHER_ID}):
        payload = context() | mutation
        client = RuntimeClient(
            settings(), httpx.MockTransport(lambda _, data=payload: httpx.Response(200, json=data)),
        )
        with pytest.raises(RuntimeBoundaryError) as caught:
            await client.resolve_context(request())
        assert "private-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_authenticated_context_returns_metadata_only() -> None:
    def handle(incoming: httpx.Request) -> httpx.Response:
        assert incoming.headers["authorization"] == "Bearer test-service-credential"
        assert incoming.headers["x-request-id"] == ID
        assert b"api_key" not in incoming.content
        return httpx.Response(200, json=context())

    result = await RuntimeClient(settings(), httpx.MockTransport(handle)).resolve_context(request())
    assert result.payer == "user_byok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 500])
async def test_http_failure_does_not_disclose_body_or_retry(status: int) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="private upstream credentials")

    client = RuntimeClient(settings(), httpx.MockTransport(handle))
    with pytest.raises(RuntimeBoundaryError) as caught:
        await client.resolve_context(request())
    assert "private upstream" not in str(caught.value)
    assert calls == 1


@pytest.mark.asyncio
async def test_domain_failure_remains_failure() -> None:
    payload = {
        "schema_version": "1", "execution_id": ID, "status": "failed", "result": None,
        "error": {"code": "FORBIDDEN", "message": "Not permitted", "request_id": ID},
    }
    client = RuntimeClient(
        settings(), httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    tool_request = ToolRequest(
        **request().model_dump(), tool_name="vocabulary.save",
        arguments={"lemma": "hello", "language": "en"}, idempotency_key=ID,
    )
    result = await client.execute_tool(tool_request)
    assert result.status == "failed"
    assert result.result is None
    payload["status"] = "succeeded"
    with pytest.raises(RuntimeBoundaryError, match="Inconsistent"):
        await client.execute_tool(tool_request)


@pytest.mark.asyncio
async def test_response_limits_and_invalid_json() -> None:
    for body in (b"x" * 4097, b"not json"):
        client = RuntimeClient(
            settings(), httpx.MockTransport(lambda _, data=body: httpx.Response(200, content=data)),
        )
        with pytest.raises(RuntimeBoundaryError):
            await client.resolve_context(request())


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await RuntimeClient(settings(), httpx.MockTransport(handle)).resolve_context(request())


def test_untrusted_plan_override_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeRequest.model_validate(request().model_dump() | {"plan": "vip"})


def test_missing_config_fails_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMAN_BULE_RUNTIME_URL", raising=False)
    with pytest.raises(RuntimeBoundaryError, match="configuration"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize("url", ["http://runtime.test", "https://user:secret@runtime.test"])
def test_invalid_transport_origin(url: str) -> None:
    configuration = settings().model_copy(update={"base_url": url})
    with pytest.raises(RuntimeBoundaryError):
        RuntimeClient(configuration)
