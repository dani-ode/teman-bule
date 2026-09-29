"""Rebuild the Chat Turn canvas with functional components and update Langflow.

Each canvas node embeds a thin Custom Component whose source imports the real
implementation from the injected global variable ``TEMAN_BULE_CHAT_COMPONENTS``.
No credential is stored in the canvas; keys live in the Langflow process env.
"""
from __future__ import annotations

import json
import urllib.request

KEY = "sk-oFBsG0WtoviiYH9rOZs2S93Djhi9egXA6LSSJrIYISM"
BASE = "http://localhost:7860"
FLOW_ID = "46c932ae-d6de-4c1e-8e2f-97a7447cf259"

# node id -> (display label, x, y)
NODES = {
    "json_input": ("Application JSON Input", 0, 220),
    "normalize_input": ("Normalize Text/Audio", 340, 220),
    "callcraft_router": ("CallCraft Tool Router", 680, 220),
    "postgres_tools": ("PostgreSQL Authorized Reads", 1020, 40),
    "astra_retriever": ("Astra RAG", 1020, 400),
    "prompt_builder": ("Persona and Context Prompt", 1360, 220),
    "ai_model": ("Configured AI Model", 1700, 220),
    "response_parser": ("Validate Structured Response", 2040, 220),
    "tts": ("ElevenLabs TTS", 2380, 40),
    "background_dispatch": ("Dispatch Chat Background", 2380, 400),
    "chat_response": ("Chat Response", 2720, 220),
}

EDGES = [
    ("json_input", "normalize_input"),
    ("normalize_input", "callcraft_router"),
    ("callcraft_router", "postgres_tools"),
    ("callcraft_router", "astra_retriever"),
    ("postgres_tools", "prompt_builder"),
    ("astra_retriever", "prompt_builder"),
    ("normalize_input", "prompt_builder"),
    ("prompt_builder", "ai_model"),
    ("ai_model", "response_parser"),
    ("response_parser", "tts"),
    ("response_parser", "background_dispatch"),
    ("response_parser", "chat_response"),
    ("tts", "chat_response"),
    ("background_dispatch", "chat_response"),
]

WRAPPER = '''"""Functional chat-turn node; implementation lives in the injected global
variable TEMAN_BULE_CHAT_COMPONENTS. Credentials stay in the process env."""
import os

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MultilineInput, Output, StrInput
from lfx.schema.data import Data


def _load_impl():
    source = None
    try:
        from lfx.services.manager import get_service_manager

        manager = get_service_manager()
        service = None
        for key in ("variable_service", "variables", "variable"):
            try:
                service = manager.get(key)
            except Exception:
                service = None
            if service is not None:
                break
        if service is not None:
            for kwargs in (
                {"name": "TEMAN_BULE_CHAT_COMPONENTS", "field": "", "session": None},
                {"name": "TEMAN_BULE_CHAT_COMPONENTS", "field": "value", "session": None},
            ):
                try:
                    source = service.get_variable(**kwargs)
                except Exception:
                    source = None
                if source:
                    break
    except Exception:
        source = None
    if not source:
        source = os.environ.get("TEMAN_BULE_CHAT_COMPONENTS")
    if not source:
        raise RuntimeError("Chat components module not available in Langflow")
    namespace = {}
    exec(source, namespace)
    return namespace["TemanBuleChatStage"]


class ChatCanvasStage(Component):
    display_name = "Teman Bule Chat Stage"
    name = "ChatCanvasStage"
    description = "Functional chat-turn stage backed by the injected module."
    inputs = [
        StrInput(name="operation", display_name="Operation", required=True),
        MultilineInput(name="payload", display_name="Application JSON", value=""),
        DataInput(name="upstream", display_name="Dependencies", is_list=True),
    ]
    outputs = [Output(name="result", display_name="Result", method="execute")]

    def execute(self) -> Data:
        impl = _load_impl()
        stage = impl.__new__(impl)
        stage.operation = self.operation
        stage.payload = self.payload
        stage.upstream = self.upstream
        return stage.execute()
'''


def _template_field(operation: str) -> dict:
    return {
        "_type": "Component",
        "code": {"type": "code", "value": WRAPPER},
        "operation": {
            "type": "str", "_input_type": "StrInput", "value": operation,
            "required": True, "name": "operation", "display_name": "Operation",
            "show": True, "advanced": False,
        },
        "payload": {
            "type": "str", "_input_type": "MultilineInput", "value": "",
            "multiline": True, "name": "payload", "display_name": "Payload",
            "show": True, "advanced": False,
        },
        "upstream": {
            "type": "other", "_input_type": "DataInput", "input_types": ["Data"],
            "list": True, "value": [], "name": "upstream",
            "display_name": "Upstream", "show": True, "advanced": False,
        },
    }


def build_node(node_id: str) -> dict:
    label, x, y = NODES[node_id]
    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": x, "y": y},
        "data": {
            "id": node_id,
            "type": "ChatCanvasStage",
            "node": {
                "display_name": label,
                "description": f"Functional: {node_id}",
                "base_classes": ["Data"],
                "field_order": ["operation", "payload", "upstream"],
                "template": _template_field(node_id),
                "outputs": [
                    {
                        "name": "result", "display_name": "Result",
                        "method": "execute", "types": ["Data"],
                        "selected": "Data", "cache": True,
                    }
                ],
                "metadata": {"implementation_status": "functional"},
            },
        },
        "selected": False,
        "measured": {"width": 320, "height": 332},
    }


def build_edge(source: str, target: str) -> dict:
    source_handle = {
        "dataType": "ChatCanvasStage", "id": source, "name": "result", "output_types": ["Data"]
    }
    target_handle = {"fieldName": "upstream", "id": target, "inputTypes": ["Data"], "type": "Data"}
    return {
        "id": f"reactflow__edge-{source}{json.dumps(source_handle, separators=(',', ':'))}-{target}{json.dumps(target_handle, separators=(',', ':'))}",
        "source": source,
        "target": target,
        "sourceHandle": json.dumps(source_handle, separators=(",", ":")),
        "targetHandle": json.dumps(target_handle, separators=(",", ":")),
        "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
        "animated": False,
        "className": "",
        "selected": False,
    }


def main() -> None:
    payload = {
        "name": "Teman Bule - Chat Turn v1",
        "description": (
            "Synchronous application chat turn. Functional build: JSON input, "
            "normalize, CallCraft routing, PostgreSQL authorized reads, Astra RAG "
            "stub, persona prompt, Gemini LLM, response validation, ElevenLabs TTS "
            "and background dispatch ack. Credentials via process env."
        ),
        "data": {
            "nodes": [build_node(n) for n in NODES],
            "edges": [build_edge(s, t) for s, t in EDGES],
            "viewport": {"x": 40, "y": 300, "zoom": 0.25},
        },
    }
    req = urllib.request.Request(
        f"{BASE}/api/v1/flows/{FLOW_ID}",
        data=json.dumps(payload).encode(),
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)
    print("PATCH", resp.status, "nodes:", len(body["data"]["nodes"]), "edges:", len(body["data"]["edges"]))
    print("name:", body["name"])


if __name__ == "__main__":
    main()
