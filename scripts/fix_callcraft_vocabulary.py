"""Correct existing project vocabulary extraction specs via MCP, without callbacks."""

import json
from pathlib import Path

import httpx
from inspect_mcp import messages


def main():
    config = json.loads(Path(".agents/mcp_config.json").read_text())["mcpServers"]["callcraft"]
    headers = {**config["headers"], "Accept": "application/json, text/event-stream"}
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
        sequence = 0

        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            response = client.post(config["serverUrl"], headers=headers, json={
                "jsonrpc": "2.0", "id": sequence, "method": method, "params": params,
            })
            response.raise_for_status()
            if response.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            envelope = next(item for item in messages(response) if item.get("id") == sequence)
            if "error" in envelope:
                raise RuntimeError("MCP rejected request")
            return envelope["result"]

        def tool(name, arguments):
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError"):
                raise RuntimeError("MCP tool failed: " + name)
            if "structuredContent" in result:
                return result["structuredContent"]
            return json.loads(result["content"][0]["text"])

        initialized = rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "temanbule-spec-correction", "version": "1"},
        })
        headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        client.post(config["serverUrl"], headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }).raise_for_status()
        specs = tool("callcraft_list_specs", {"project_id": config["headers"]["X-PROJECT-ID"]})
        targets = {
            "Teman Bule - Vocabulary Save v1",
            "Teman Bule - Vocabulary Get v1",
            "Teman Bule - Vocabulary Update Status v1",
        }
        for spec in specs["specs"]:
            if spec["name"] not in targets:
                continue
            if spec["projectId"] != config["headers"]["X-PROJECT-ID"]:
                raise RuntimeError("Project mismatch")
            schema = spec["requestSchema"]
            if isinstance(schema, str):
                schema = json.loads(schema)
            prompt = (
                "Return only domain function arguments matching the response schema. "
                "Preserve supplied identifiers exactly. Do not invent missing required values. "
                "The Teman Bule backend validates authorization and executes the function after "
                "receiving this JSON. You do not read or write its database."
            )
            negative = (
                "Never return a fabricated resource_id, resource_version, saved entry, "
                "or persistence success. Never infer an owner, grant, credential, or callback URL."
            )
            tools_config = {"enabled": True, "toolChoice": "auto", "tools": [{
                "name": "extract_vocabulary_arguments",
                "description": "Extract arguments for " + spec["name"],
                "agentRole": "Structured function argument extractor",
                "textContext": prompt, "includeImageContext": False,
            }]}
            tool("callcraft_update_spec", {
                "spec_id": spec["id"], "request_schema": schema, "response_schema": schema,
                "positive_prompt": prompt, "negative_prompt": negative,
                "allow_additional_prompt": False, "tools_config": tools_config,
            })
            contract = tool("callcraft_get_call_contract", {"spec_id": spec["id"]})
            actual = contract["responseSchema"]
            if isinstance(actual, str):
                actual = json.loads(actual)
            if actual != schema or contract["executionMode"] != "extraction":
                raise RuntimeError("Readback mismatch: " + spec["name"])
            print("Verified extraction arguments:", spec["name"])
            targets.remove(spec["name"])
        if targets:
            raise RuntimeError("Missing expected vocabulary specs")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, StopIteration) as exc:
        print("Spec correction failed:", type(exc).__name__)
        raise SystemExit(1) from None
