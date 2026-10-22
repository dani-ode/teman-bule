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
async def test_workflow_v2_ingestion_background_dispatch_acceptance():
    """Ingestion dispatch: mode background, envelope ramping (pull), return job_id."""
    async def resolver(purpose, version):
        assert (purpose, version) == ("conversation_ingestion", "conv-ing.v1")
        return FlowBinding(
            "flow-1", "conv-ing.v1", "1", "1", 300000,
            input_tweak_component="Webhook-wcndY",
        )

    def respond(request):
        assert request.url.path == "/api/v2/workflows"
        payload = json.loads(request.content)
        assert payload["flow_id"] == "flow-1"
        assert payload["mode"] == "background"
        assert payload["session_id"] == "session-1"
        # Pull: envelope hanya reference — isi pesan ditarik flow via MCP.
        envelope = json.loads(payload["tweaks"]["Webhook-wcndY"]["data"])
        assert envelope == {
            "schema_version": "extraction.v1",
            "flow_version": "conv-ing.v1",
            "session_id": "session-1",
            "owner_user_id": "user-1",
            "source_start": 1,
            "source_end": 50,
        }
        return httpx.Response(
            202, json={"object": "job", "status": "queued", "job_id": "lf-job-1"}
        )

    adapter = LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url="https://langflow.example", api_key="test",
            run_path="/api/v2/workflows",
        ), resolver=resolver, transport=httpx.MockTransport(respond),
    )
    job_id = await adapter.extract(
        session_id="session-1",
        owner_user_id="user-1",
        source_start=1,
        source_end=50,
        schema_version="extraction.v1",
        flow_version="conv-ing.v1",
    )
    assert job_id == "lf-job-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"object": "job", "status": "failed", "job_id": "lf-job-1"},  # tidak queued
        {"object": "job", "status": "queued"},  # tanpa job_id
        {"status": "queued", "job_id": "lf-job-1"},  # tanpa object=job
    ],
)
async def test_workflow_v2_ingestion_background_acceptance_invalid(body):
    async def resolver(purpose, version):
        return FlowBinding(
            "flow-1", "conv-ing.v1", "1", "1", 300000,
            input_tweak_component="Webhook-wcndY",
        )

    def respond(request):
        return httpx.Response(202, json=body)

    adapter = LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url="https://langflow.example", api_key="test",
            run_path="/api/v2/workflows",
        ), resolver=resolver, transport=httpx.MockTransport(respond),
    )
    with pytest.raises(DependencyUnavailableError):
        await adapter.extract(
            session_id="session-1", owner_user_id="user-1",
            source_start=1, source_end=50, schema_version="extraction.v1",
            flow_version="conv-ing.v1",
        )


@pytest.mark.asyncio
async def test_workflow_v2_ingestion_requires_pinned_input_component():
    """Registry tanpa input_tweak_component → FLOW_NOT_CONFIGURED."""
    async def resolver(purpose, version):
        return FlowBinding("flow-1", "conv-ing.v1", "1", "1", 300000)

    def respond(request):  # pragma: no cover - tidak boleh terpanggil
        raise AssertionError("HTTP tidak boleh dipanggil tanpa pin komponen")

    adapter = LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url="https://langflow.example", api_key="test",
            run_path="/api/v2/workflows",
        ), resolver=resolver, transport=httpx.MockTransport(respond),
    )
    with pytest.raises(DependencyUnavailableError) as excinfo:
        await adapter.extract(
            session_id="session-1", owner_user_id="user-1",
            source_start=1, source_end=50, schema_version="extraction.v1",
            flow_version="conv-ing.v1",
        )
    assert excinfo.value.code == "FLOW_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_workflow_v2_purpose_flow_sync_terminal_result():
    """Purpose flow (facts/assessment) tetap mode sync via run_purpose_flow."""
    async def resolver(purpose, version):
        assert (purpose, version) == ("user_fact_extraction", "fact-ext.v1")
        return FlowBinding("flow-2", "fact-ext.v1", "1", "1", 60000)

    def respond(request):
        payload = json.loads(request.content)
        assert payload["flow_id"] == "flow-2"
        assert payload["mode"] == "sync"
        return httpx.Response(200, json={
            "status": "completed", "has_errors": False,
            "output": {"text": '{"facts":[]}'},
        })

    adapter = LangflowExtractionAdapter(
        LangflowExtractionConfig(
            base_url="https://langflow.example", api_key="test",
            run_path="/api/v2/workflows",
        ), resolver=resolver, transport=httpx.MockTransport(respond),
    )
    result = await adapter.run_purpose_flow(
        purpose="user_fact_extraction",
        input_data={"flow_version": "fact-ext.v1", "session_id": "s1"},
        session_id="s1",
    )
    assert result == {"facts": []}


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
