"""Check draft artifact syntax, local references and blueprint allowlist parity."""

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check_references(value: Any, origin: Path) -> None:
    if isinstance(value, list):
        for item in value:
            check_references(item, origin)
    elif isinstance(value, dict):
        for reference in value.get("blueprint_refs", []):
            if not (ROOT / reference).is_file():
                raise ValueError(f"Missing blueprint reference: {reference}")
        reference = value.get("$ref")
        if reference and not reference.startswith("${"):
            filename, _, fragment = reference.partition("#")
            target = load(origin.parent / filename) if filename else load(origin)
            for part in fragment.lstrip("/").split("/") if fragment else []:
                target = target[part.replace("~1", "/").replace("~0", "~")]
        for item in value.values():
            check_references(item, origin)


def main() -> None:
    paths = sorted((ROOT / "custom_callcraft_spec").glob("*.json"))
    paths += sorted((ROOT / "custom_langflow_components").glob("*.json"))
    for path in paths:
        check_references(load(path), path)
    catalog = load(ROOT / "custom_callcraft_spec/tools.v1.json")
    flows = load(ROOT / "custom_langflow_components/flows.v1.json")["flows"]
    tools = {tool["name"]: tool for tool in catalog["tools"]}
    if len(tools) != len(catalog["tools"]):
        raise ValueError("Duplicate tool names")
    aliases = {name.replace(".", "_") for name in tools}
    if len(aliases) != len(tools):
        raise ValueError("MCP tool alias collision")
    tool_blueprint = (ROOT / ".blueprint/callcraft-tools.md").read_text()
    rows = re.findall(
        r"^\| `([^`]+)` \| `POST ([^`]+)` \| `([^`]+)` \| (Required|Not required) \|",
        tool_blueprint,
        re.MULTILINE,
    )
    if {row[0] for row in rows} != set(tools):
        raise ValueError("Tool catalog differs from blueprint")
    for name, endpoint, scope, idempotency in rows:
        tool = tools[name]
        expected = "required" if idempotency == "Required" else "not_required"
        if (tool["endpoint"], tool["scope"], tool["idempotency"]) != (endpoint, scope, expected):
            raise ValueError(f"Blueprint execution contract mismatch: {name}")
    flow_blueprint = (ROOT / ".blueprint/langflow-flows.md").read_text()
    names = re.findall(r"^\| `([^`]+)` \|", flow_blueprint, re.MULTILINE)
    if set(names) != {flow["purpose"] for flow in flows} or len(names) != len(flows):
        raise ValueError("Flow catalog differs from blueprint")
    for flow in flows:
        allowed = {name for name, tool in tools.items() if flow["purpose"] in tool["purposes"]}
        if allowed != set(flow["tools"]):
            raise ValueError(f"Flow/tool allowlist mismatch: {flow['purpose']}")
    if tools["learning.record_progress"]["purposes"]:
        raise ValueError("Progress writes require an explicit blueprint policy change")
    print(f"Validated {len(paths)} JSON artifacts, {len(tools)} tools, {len(flows)} flows, local references and blueprint parity.")
    print("This check does not certify JSON Schema semantics or vendor import/runtime compatibility.")


if __name__ == "__main__":
    main()
