"""A tiny MCP server over stdio, used by the MCP tests.

python tests/fixtures/echo_mcp_server.py
"""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("echo")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool()
def shout(text: str) -> str:
    """Upper-case the text."""
    return text.upper()


@server.tool()
def secret_env() -> str:
    """Report whether HUB_TEST_TOKEN reached the server."""
    return os.environ.get("HUB_TEST_TOKEN", "<missing>")


if __name__ == "__main__":
    server.run()
