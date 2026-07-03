"""The single integration point with the upstream vendor MCP server.

Everything that knows *how* to reach the vendor lives here: the proxy that
forwards passthrough tools, and the client used by composite tools for their
internal vendor calls. Both come from one Vendor handle, so swapping the
upstream (e.g. to Slack's official mcp.slack.com server) touches only .env
and the allowlist in overrides.py.
"""

import json
from dataclasses import dataclass
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import FastMCPProxy

from slack_mcp_wrapper.config import Settings


@dataclass(frozen=True)
class Vendor:
    """Where the vendor server is and how to talk to it.

    ``target`` is a URL in production; tests may pass an in-memory FastMCP
    instance instead (FastMCP's Client accepts both).
    """

    target: str | FastMCP
    api_key: str | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "Vendor":
        return cls(target=settings.vendor_slack_mcp_url, api_key=settings.vendor_api_key)

    def client(self) -> Client:
        """A fresh MCP client session; composite tools open one per call so
        their traffic stays isolated from the passthrough proxy's session."""
        # auth is only meaningful (and only accepted) for network transports.
        if isinstance(self.target, str):
            return Client(self.target, auth=self.api_key)
        return Client(self.target)

    def proxy(self) -> FastMCPProxy:
        """The proxy that sources the vendor's tools for forwarding.

        Connection is lazy: the vendor is first contacted when an AI client
        initializes against the wrapper, so the wrapper boots vendor-down.
        """
        return create_proxy(self.client(), name="slack-vendor")

    async def call_tool(self, name: str, args: dict[str, Any]) -> CallToolResult:
        """One vendor tool call by *raw* vendor tool name (no namespace)."""
        async with self.client() as client:
            return await client.call_tool(name, args)


def extract_payload(result: CallToolResult) -> Any:
    """Best-effort structured payload from a vendor tool result.

    Prefers the structured/deserialized forms; falls back to parsing text
    content as JSON, since vendor servers vary in what they populate.
    """
    if result.data is not None:
        return result.data
    if result.structured_content is not None:
        payload = result.structured_content
        # FastMCP wraps non-object outputs as {"result": ...}
        if isinstance(payload, dict) and set(payload) == {"result"}:
            return payload["result"]
        return payload
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    raise ToolError("Vendor tool returned no usable content.")
