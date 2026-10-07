"""Transport doubles untuk LangflowPodcastAdapter; bukan uji vendor live.

Menutup kontrak:
- Ingestion: POST /api/v2/workflows mode background, tweak Webhook-iGh25,
  acceptance queued + job_id; poll GET ?job_id= hingga completed page_count>0.
- Script generation: mode sync, tweak Webhook-qTk7h, output tervalidasi dua
  speaker Elean/Willy + citations; kegagalan → DependencyUnavailableError.
"""

from __future__ import annotations

import json

import httpx
import pytest

from temanbule.modules.catalog.flows import FlowBinding
from temanbule.modules.podcasts.langflow_adapter import LangflowPodcastAdapter
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


def _adapter(respond) -> LangflowPodcastAdapter:
    return LangflowPodcastAdapter(
        Settings(_env_file=None, langflow_api_key="synthetic"),
        httpx.MockTransport(respond),
    )


BINDING = FlowBinding("registered-flow", "podcast-ing.v1", "1", "1", 5000)


@pytest.mark.asyncio
async def test_ingestion_dispatch_v2_background_schema() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/v2/workflows"
        assert body["flow_id"] == "registered-flow"
        assert body["mode"] == "background"
        payload = json.loads(body["tweaks"]["Webhook-iGh25"]["data"])
        assert payload["purpose"] == "podcast_document_ingestion"
        assert payload["podcast"]["source_version_id"] == "src-1"
        return httpx.Response(
            200,
            json={
                "job_id": "job-123",
                "flow_id": "registered-flow",
                "object": "job",
                "status": "queued",
                "errors": [],
            },
        )

    job_id = await _adapter(respond).trigger_document_ingestion(
        BINDING,
        {
            "purpose": "podcast_document_ingestion",
            "podcast": {"podcast_id": "p1", "source_version_id": "src-1"},
        },
    )
    assert job_id == "job-123"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "not_queued", "no_job_id"])
async def test_ingestion_dispatch_rejects_bad_acceptance(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "http":
            return httpx.Response(503, json={"detail": "down"})
        body: dict[str, object] = {
            "job_id": "job-123",
            "object": "job",
            "status": "queued",
            "errors": [],
        }
        if failure == "not_queued":
            body["status"] = "failed"
        if failure == "no_job_id":
            body.pop("job_id")
        return httpx.Response(200, json=body)

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).trigger_document_ingestion(BINDING, {"podcast": {}})
    assert error.value.code == "PODCAST_INGESTION_DISPATCH_UNKNOWN"


@pytest.mark.asyncio
async def test_ingestion_poll_completed_returns_output() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["job_id"] == "job-123"
        return httpx.Response(
            200,
            json={
                "job_id": "job-123",
                "status": "completed",
                "errors": [],
                "output": {"page_count": 12, "chunk_count": 40},
            },
        )

    output = await _adapter(respond).poll_document_ingestion(
        job_id="job-123", timeout_ms=5000
    )
    assert output["page_count"] == 12


@pytest.mark.asyncio
async def test_ingestion_poll_running_is_retriable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job_id": "job-123", "status": "running", "errors": []})

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).poll_document_ingestion(job_id="job-123", timeout_ms=5000)
    assert error.value.code == "PODCAST_INGESTION_RUNNING"


@pytest.mark.asyncio
async def test_ingestion_poll_terminal_failure() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"job_id": "job-123", "status": "failed", "errors": ["parse error"]},
        )

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).poll_document_ingestion(job_id="job-123", timeout_ms=5000)
    assert error.value.code == "PODCAST_INGESTION_FAILED"


@pytest.mark.asyncio
async def test_script_generation_sync_validated() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["mode"] == "sync"
        payload = json.loads(body["tweaks"]["Webhook-qTk7h"]["data"])
        assert payload["purpose"] == "podcast_script_generation"
        output = {
            "outline": "Pembahasan paper",
            "estimated_duration_seconds": 420,
            "segments": [
                {"speaker": "elean", "text": "Halo Willy...", "citations": ["chunk-1"]},
                {"speaker": "willy", "text": "Halo Elean...", "citations": ["chunk-2"]},
            ],
        }
        return httpx.Response(
            200,
            json={"status": "completed", "has_errors": False, "errors": [], "output": output},
        )

    result = await _adapter(respond).run_script_generation(
        BINDING,
        {"purpose": "podcast_script_generation", "input": {"target_duration_seconds": 480}},
    )
    assert result.outline == "Pembahasan paper"
    assert [s.speaker for s in result.segments] == ["elean", "willy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "errors", "single_speaker", "no_citations"])
async def test_script_generation_rejects_invalid_output(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "http":
            return httpx.Response(500, json={"detail": "boom"})
        output: dict[str, object] = {
            "outline": "Pembahasan",
            "segments": [
                {"speaker": "elean", "text": "Satu", "citations": ["c1"]},
                {"speaker": "willy", "text": "Dua", "citations": ["c2"]},
            ],
        }
        if failure == "single_speaker":
            output["segments"] = [
                {"speaker": "elean", "text": "Satu", "citations": ["c1"]},
                {"speaker": "elean", "text": "Dua", "citations": ["c2"]},
            ]
        if failure == "no_citations":
            output["segments"] = [
                {"speaker": "elean", "text": "Satu", "citations": []},
                {"speaker": "willy", "text": "Dua", "citations": ["c2"]},
            ]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "has_errors": failure == "errors",
                "errors": ["x"] if failure == "errors" else [],
                "output": output,
            },
        )

    with pytest.raises(DependencyUnavailableError) as error:
        await _adapter(respond).run_script_generation(BINDING, {"purpose": "p"})
    assert error.value.code == "PODCAST_SCRIPT_OUTCOME_UNKNOWN"
