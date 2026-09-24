"""Start the Langflow MCP proxy using this project's local credentials."""

import os
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    config = {
        **dotenv_values(Path(__file__).resolve().parent.parent / ".env"),
        **os.environ,
    }
    url = config.get("LANGFLOW_MCP_STREAMABLE_URL")
    if not url:
        raise SystemExit("LANGFLOW_MCP_STREAMABLE_URL is required in .env or environment")
    args = ["uvx", "--with", "mcp<2", "mcp-proxy", "--transport", "streamablehttp"]
    if api_key := config.get("LANGFLOW_API_KEY"):
        args.extend(["--headers", "x-api-key", api_key])
    args.append(url)
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
