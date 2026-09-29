"""Build draft Langflow canvas exports from the chat design artifacts.

Exports carry embedded component code and typed handles. This is a structural
conversion, not implementation of the stages named in the design documents.
"""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "custom_langflow_components"


def build(design, code):
    nodes = []
    for index, stage in enumerate(design["nodes"]):
        fields = {
            "operation": {"type": "str", "_input_type": "StrInput", "value": stage["id"] if stage["id"] not in {"json_input", "normalize_input", "chat_response"} else {"json_input": "json_input", "normalize_input": "normalize_input", "chat_response": "response"}[stage["id"]], "required": True},
            "payload": {"type": "str", "_input_type": "MultilineInput", "value": "", "multiline": True},
            "upstream": {"type": "other", "_input_type": "DataInput", "input_types": ["Data"], "list": True, "value": []},
        }
        for name, field in fields.items():
            field.update(name=name, display_name=name.title(), show=True, advanced=False)
        nodes.append({
            "id": stage["id"], "type": "genericNode",
            "position": {"x": index * 360, "y": 200},
            "data": {"id": stage["id"], "type": "ChatCanvasStage", "node": {
                "display_name": stage["label"], "description": "Draft: " + stage["component"],
                "base_classes": ["Data"], "field_order": list(fields),
                "template": {"_type": "Component", "code": {"type": "code", "value": code}, **fields},
                "outputs": [{"name": "result", "display_name": "Result", "method": "execute", "types": ["Data"], "selected": "Data", "cache": True}],
                "metadata": {"implementation_status": "input_only"},
            }},
        })
    edges = []
    for index, connection in enumerate(design["edges"]):
        source = {"id": connection["source"], "dataType": "ChatCanvasStage", "name": "result", "output_types": ["Data"]}
        target = {"id": connection["target"], "fieldName": "upstream", "inputTypes": ["Data"], "type": "other"}
        edges.append({
            "id": f"edge-{index}", **connection,
            "sourceHandle": json.dumps(source).replace('"', "œ"),
            "targetHandle": json.dumps(target).replace('"', "œ"),
            "data": {"sourceHandle": source, "targetHandle": target},
        })
    return {"name": design["name"] + " (DRAFT - incomplete)",
            "description": "Structural draft; only input parsing is implemented. Do not activate.",
            "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
            "is_component": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    code = (DIRECTORY / "chat_canvas_component.py").read_text()
    for stem in ("chat-workflow", "chat-background-workflow"):
        design = json.loads((DIRECTORY / f"{stem}.v1.json").read_text())
        result = build(design, code)
        destination = DIRECTORY / f"{stem}.canvas.v1.json"
        serialized = json.dumps(result, indent=2) + "\n"
        if args.check:
            assert destination.read_text() == serialized, f"Stale export: {destination.name}"
        else:
            destination.write_text(serialized)
        print(destination.name)
