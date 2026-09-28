---
trigger: model_decision
description: Background data processing tasks and workflows
---

- **Core Processing:** Establish LangFlow as the centralized engine for data processing operations.
- **Component Customization:** Define and implement custom components tailored to system requirements inside the `custom_langflow_components` directory.
- **MCP Control:** Manage and interact with these data processing workflows dynamically via MCP servers.

Workflow API (synchronous execution):
path: "/api/v2/workflows"
method: POST
body:
  {
    "flow_id": "<flow_id>",
    "mode": "sync",
    "tweaks": {
    "<component_id>": {
        "<key>": "<value>"
      }
    }
  }

Webhook trigger (separate Langflow v1 endpoint):
path: "/api/v1/webhook/<flow_id>"
method: POST

The Workflow API is the application execution path. It is not a webhook and
should not be renamed to a v2 webhook. Use the webhook endpoint only when an
external system must trigger a flow through Langflow's webhook contract.