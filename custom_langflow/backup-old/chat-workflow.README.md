# Chat workflow artifacts

## Status

`chat-workflow.v1.json` and `chat-background-workflow.v1.json` are design
specifications. Generated structural canvas exports are:

- `chat-workflow.canvas.v1.json`
- `chat-background-workflow.canvas.v1.json`

Regenerate them with `python scripts/export_chat_canvas.py`; CI-style drift can
be checked using `--check`. The generated canvas embeds
`chat_canvas_component.py`, has typed handles and is suitable for Langflow
import validation, but only its JSON input stage is implemented. Every other
stage fails explicitly. It must not be registered as an active deployment.

## Execution ownership

1. Frontend calls the authenticated backend REST endpoint with text or audio.
2. Backend checks session ownership and queries PostgreSQL for the active
   `ai_flow_registry` entry, session/runtime snapshot, persona, model and tool
   bindings. No per-flow environment variable is needed.
3. Backend sends the resolved context to Langflow Workflow API v2 in sync mode.
4. Langflow orchestrates transcription, CallCraft selection, authorized SQL
   reads, Astra retrieval, prompt construction, model generation and TTS.
5. Langflow commits the canonical user/assistant turn before dispatching
   background processing using the persisted message IDs. The dispatch waits
   only for durable acceptance, not processing completion.
6. Backend validates the workflow response and returns it to the frontend.

Infrastructure adapters may provide scoped database/storage access, but the
backend endpoint must not duplicate the workflow's business orchestration.

## Work still required for an importable export

- Implement referenced custom components, including real STT, TTS and model IO.
- Replace logical node definitions with Langflow `data.nodes` containing actual
  component templates/code, typed output handles and matching edge handles.
- Add a canonical turn commit node before background dispatch; the draft graph
  currently does not implement that dependency.
- Align input contracts with envelope v3, including execution references and
  concrete validated audio alternatives; the draft schemas are incomplete.
- Bind registry field names to the actual SQL models rather than treating the
  illustrative select lists as executable SQL.
- Validate/import against the deployed Langflow component schema and execute
  text/audio cases before activating a registry version.
