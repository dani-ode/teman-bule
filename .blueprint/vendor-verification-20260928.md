# Account metadata verification — 2026-09-28

## Architecture correction (supersedes callback conclusions below)

Per explicit product-owner clarification, CallCraft is REST input → structured
JSON output. Backend Teman Bule validates and performs domain operations after
the caller receives that JSON. Extraction mode is intentional; HTTP vendor
callback binding and a publicly reachable backend are not activation requirements.
`CALLCRAFT_INTERNAL_TOOL_BASE_URL` has been removed from settings/environment and
the runtime dispatch gate. Historical probe observations below remain evidence,
but the conclusion that extraction must migrate to vendor HTTP mode is withdrawn.
Existing remote spec output schemas still need to describe extracted arguments
rather than fabricated persistence IDs; this local correction does not mutate them.

Actual authenticated HTTP requests, using local credentials without printing them:

| Probe | Result |
|---|---|
| Gemini GET `/v1beta/models` | HTTP 200; contains gemini-3.8-flash, gemini-3.5-transcribe, gemini-embedding-001 |
| ElevenLabs GET `/v1/models` | HTTP 200 |
| ElevenLabs GET `/v1/voices/{configured Elean ID}` | HTTP 200 |
| ElevenLabs GET `/v1/voices/{configured Willy ID}` | HTTP 200 |
| OpenAI GET `/v1/models/text-embedding-3-small` | HTTP 200 |
| Configured Langflow GET `/api/v1/version` | Transport unavailable from development host |
| Configured Langflow GET `/api/v1/flows/` | Transport unavailable from development host |
| Google public pricing documentation | Transport unavailable; no verified live rate imported |
| Langflow TCP from development host | Hostname resolution fails (`gaierror`), configured port 7861 |
| Langflow version from running API container | `ConnectError`; not just a host-only path issue |

This supersedes the earlier claim that model existence/access was unverified.
It does not establish generation success, account quota, pricing, final usage
metering, voice/model compatibility, or realtime SDK support. No billable
generation was performed in these probes.

SQL seed revision 20260928 preserves existing agent identities and adds immutable
persona artifacts and separate voice bindings. Persona files must be available
to the runtime artifact publisher; a repository reference alone is not proof
that a deployed Langflow flow loaded the prompt.

The two configured runtime Gemini models are now seeded as staged catalog revisions.
`gemini_generate_content` is their intended adapter binding, not evidence of a
working deployed executor. No live rate card is published from unverified prices.

No Langflow container appears in the local running-container inventory. This does
not establish whether the intended deployment is remote or stopped. A reachable
deployment endpoint is required before flow discovery/import can proceed.

## MCP follow-up: corrected discovery

Using `.agents/mcp_config.json`, both MCP servers initialize successfully.
Langflow project exposes `vector_store_rag`, `teman_bule_local_echo`, `temanbule`.
All take optional `input_value` and `session_id`; none exposes flow management.
The HTTP origin discovered from that MCP configuration returns Langflow **1.11.6**.
Authenticated management discovery confirms these existing flow IDs match `.env`:

- conversation ingestion: `9cce08d2-844d-40ec-b921-99436e8cf8c1`
- user fact extraction: `7ba24e9f-5b5c-41cc-995e-e6b64732cc26`
- learning assessment: `14090c32-e61a-4e76-b1e1-7ded42dfcf2e`

The management origin is reachable from the host on localhost:7860, but the
running API container cannot connect to host.docker.internal:7860 or its configured
7861 endpoint. Do not replace the container URL with localhost:7860: that would
address the API container itself. Runtime connectivity remains a separate blocker.

CallCraft MCP capabilities confirms HTTP and extraction modes, no automatic
retries or autonomous planning. Vocabulary Save contract currently returns
`executionMode=extraction`, not HTTP persistence. Required call headers are
Authorization, X-USER-ID, X-CALL-PUBLIC-KEY and X-CALL-SPEC-ID.
Existing Vocabulary Get and Update Status specs have empty toolsConfig.
No remote spec was overwritten or activated during this read-only discovery.
