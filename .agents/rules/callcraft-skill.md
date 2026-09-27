---
trigger: model_decision
description: callcraft
---

---
name: callcraft
description: Use when integrating a project with CallCraft, managing Call Specs through its MCP server, or executing structured extraction and registered HTTP backend tools. Do not use for unrelated coding tasks.
---

# CallCraft integration

## Start with discovery

1. Connect to the deployment's MCP endpoint using credentials from the IDE's
   environment or secret store. Never put credentials in prompts or source files.
2. Call `callcraft_get_capabilities` and `callcraft_get_integration_guide`, or read
   the MCP resource `callcraft://integration`. Treat the deployed capability
   response as authoritative; do not assume local features are deployed.
3. List project specs with `callcraft_list_specs`; inspect an existing spec before
   creating another. Use `callcraft_get_call_contract` for its invocation contract.

## Choose the correct execution mode

- **Extraction:** provider function calling returns structured data. This does not
  prove that a domain action such as saving a record occurred.
- **HTTP:** explicit `toolsConfig.execution` dispatches arguments to a registered
  backend without an AI inference call. The backend owns authorization, domain
  state and durable idempotency. URLs and credential references belong in trusted
  configuration, never AI-selected runtime arguments.

## Manage and verify specs

1. Inspect the MCP tool's input schema; existing management arguments use snake_case,
   while spec documents and public envelopes use camelCase.
2. Work only in the credential's project. A project argument cannot override it.
3. For HTTP specs, run `callcraft_validate_spec` before saving. It checks the
   binding/schema against deployment policy without invoking a backend or model.
4. Create/update through MCP and retrieve/export the saved spec to verify it.
   Inspect `isError`; never infer success from the HTTP status alone.
5. Execute through the documented `/v1/call` contract. Supply project API headers,
   `X-CALL-SPEC-ID`, and mutation `Idempotency-Key`. Trusted execution context is
   supplied by the host application outside model-visible arguments.
6. On timeout or `outcomeUnknown`, reconcile with the backend using the original
   idempotency identity. Never generate a new key and blindly retry a mutation.

## Boundaries

- Do not fabricate persisted IDs, citations, tool outcomes, provider models or prices.
- Do not export or log secrets, bearer tokens, or customer inference payloads.
- Do not claim online verification based on local mocks or SDK tests.
- Request explicit intent before billable inference or destructive domain actions.
- Automatic multi-step AI planning and durable execution reconciliation are not
  provided by the current HTTP execution adapter.

## Maintainer references

When working in the CallCraft source repository, read `.blueprint/AI-SPEC.md` and
`.blueprint/specifications/mcp-http-tools.md`. The portable instructions here are
an integration entry point; blueprint documents govern implementation changes.