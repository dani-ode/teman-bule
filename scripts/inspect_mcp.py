"""Read-only MCP initialize/tools-list using project config; never print credentials."""

import argparse
import json
from pathlib import Path

import httpx


def messages(response):
    if "text/event-stream" in response.headers.get("content-type", ""):
        return [json.loads(line[5:].strip()) for line in response.text.splitlines()
                if line.startswith("data:") and line[5:].strip()]
    return [response.json()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", action="store_true")
    parser.add_argument("--server")
    parser.add_argument("--tool")
    parser.add_argument("--arguments", default="{}")
    options = parser.parse_args()
    servers = json.loads(Path(".agents/mcp_config.json").read_text())["mcpServers"]
    for name, config in servers.items():
        if options.server and name != options.server:
            continue
        url = config.get("serverUrl")
        headers = dict(config.get("headers", {}))
        if not url:
            args = config.get("args", [])
            urls = [arg for arg in args if arg.startswith(("http://", "https://"))]
            if not urls:
                print(name, "unsupported transport")
                continue
            url = urls[-1]
            if "--headers" in args:
                position = args.index("--headers")
                headers[args[position + 1]] = args[position + 2]
        headers["Accept"] = "application/json, text/event-stream"
        try:
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = client.post(url, headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "temanbule-inspector", "version": "1"},
                    },
                })
                print(name, "initialize HTTP", response.status_code)
                response.raise_for_status()
                result = next((item.get("result") for item in messages(response)
                               if item.get("id") == 1), None)
                if not result:
                    print(name, "initialize returned no result")
                    continue
                headers["MCP-Protocol-Version"] = result["protocolVersion"]
                if response.headers.get("mcp-session-id"):
                    headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
                client.post(url, headers=headers, json={
                    "jsonrpc": "2.0", "method": "notifications/initialized",
                }).raise_for_status()
                cursor = None
                while True:
                    response = client.post(url, headers=headers, json={
                        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                        "params": {"cursor": cursor} if cursor else {},
                    })
                    response.raise_for_status()
                    result = next((item.get("result") for item in messages(response)
                                   if item.get("id") == 2), None)
                    if result is None:
                        print(name, "tools/list returned no result")
                        break
                    for tool in result.get("tools", []):
                        print(name, "tool:", tool["name"])
                        if options.schemas:
                            print(json.dumps(tool.get("inputSchema", {})))
                    cursor = result.get("nextCursor")
                    if not cursor:
                        break
                if options.tool:
                    if options.tool not in {
                        "callcraft_get_capabilities", "callcraft_get_call_contract",
                        "callcraft_list_specs", "callcraft_get_spec",
                        "callcraft_get_integration_guide",
                    }:
                        raise ValueError("Only read-only contract tools supported")
                    response = client.post(url, headers=headers, json={
                        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": options.tool,
                                   "arguments": json.loads(options.arguments)},
                    })
                    response.raise_for_status()
                    output = json.dumps(messages(response))
                    # Redact configured credentials if repeated by a vendor response.
                    for value in config.get("headers", {}).values():
                        output = output.replace(value, "[REDACTED]")
                    print(output)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(name, "inspection failed:", type(exc).__name__)


if __name__ == "__main__":
    main()
