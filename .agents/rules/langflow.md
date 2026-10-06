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

Workflow API (background execution, same v2 endpoint — NOT v1):
path: "/api/v2/workflows"
method: POST
body:
  {
    "flow_id": "<flow_id>",
    "mode": "background",
    "tweaks": {
    "<component_id>": {
        "<key>": "<value>"
      }
    }
  }

Background response returns `job_id` with `links` for polling:
- status/progress: GET "/api/v2/workflows?job_id=<job_id>"
- events: GET "/api/v2/workflows/<job_id>/events"
- stop: POST "/api/v2/workflows/stop"

Acceptance (`job_id`) is not completion; poll status until terminal state.

Webhook trigger (separate Langflow v1 endpoint):
path: "/api/v1/webhook/<flow_id>"
method: POST

The Workflow API is the application execution path. It is not a webhook and
should not be renamed to a v2 webhook. Use the webhook endpoint only when an
external system must trigger a flow through Langflow's webhook contract.