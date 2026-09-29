"""Regression tests for Workflow v2 envelopes and reduced Gemini vectors."""

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.knowledge.embedding_adapter import (
    DualEmbeddingAdapter,
    DualEmbeddingConfig,
    EmbeddingProfileSpec,
)
from temanbule.modules.knowledge.langflow_adapter import (
    LangflowExtractionAdapter,
    LangflowExtractionConfig,
)
from temanbule.platform.errors import DependencyUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("status,has_errors", [("completed", False), ("failed", True)])
async def test_workflow_v2_request_and_terminal_result(status, has_errors):
    async def resolver(purpose, version):
        assert (purpose, version) == ("conversation_ingestion", "1")
        return FlowBinding("flow-1", "1", "1", "1", 60000)

    def respond(request):
        assert request.url.path == "/api/v2/workflows"
        payload = json.loads(request.content)
        assert payload["flow_id"] == "flow-1"
        assert payload["mode"] == "sync"
        assert payload["session_id"] == "session-1"
        return httpx.Response(200, json={
            "status": status, "has_errors": has_errors,
            "output": {"text": '{"summary":"hello"}'},
        })

    adapter = LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url="https://langflow.example", api_key="test",
            run_path="/api/v2/workflows",
        ), resolver=resolver, transport=httpx.MockTransport(respond),
    )
    if has_errors:
        with pytest.raises(DependencyUnavailableError):
            await adapter.extract(
                session_id="session-1", messages=[], schema_version="1", flow_version="1"
            )
    else:
        assert await adapter.extract(
            session_id="session-1", messages=[], schema_version="1", flow_version="1"
        ) == {"summary": "hello"}


@pytest.mark.asyncio
async def test_gemini_requests_profile_dimension_and_normalizes():
    def respond(request):
        payload = json.loads(request.content)
        assert payload["outputDimensionality"] == 2
        assert payload["taskType"] == "RETRIEVAL_DOCUMENT"
        return httpx.Response(200, json={"embedding": {"values": [3.0, 4.0]}})

    adapter = DualEmbeddingAdapter(
        DualEmbeddingConfig(
            gemini_api_key="test", gemini_base_url="https://gemini.example",
            openai_api_key="test", openai_base_url="https://openai.example",
            astra_api_endpoint="https://astra.example",
            astra_application_token="test",  # noqa: S106 - synthetic transport fixture
            astra_namespace="default", max_attempts=1, request_timeout_seconds=1,
        ), transport=httpx.MockTransport(respond),
    )
    profile = EmbeddingProfileSpec(
        id="profile", provider="gemini", model="gemini-embedding-001", model_revision=1,
        dimension=2, document_task_type="RETRIEVAL_DOCUMENT", generation=1,
        physical_collection_name="test",
    )
    vector = await adapter._embed_gemini(chunk_text="hello", profile=profile)
    assert vector == pytest.approx([0.6, 0.8])
