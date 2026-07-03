"""The single integration point with the upstream vendor MCP server.

Everything that knows *how* to reach the vendor lives here: the proxy that
forwards passthrough tools, and the client used by composite tools for their
internal vendor calls. Both come from one Vendor handle, so swapping the
upstream (e.g. to Slack's official mcp.slack.com server) touches only .env
and the allowlist in overrides.py.
"""

import csv
import io
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
        """One vendor tool call by *raw* vendor tool name (no namespace).

        Vendor-side failures raise ToolError carrying the vendor's own
        message (e.g. "channel_not_found") — callers never receive an
        is_error result to parse.
        """
        async with self.client() as client:
            return await client.call_tool(name, args)


# Column mapping observed live against korotovsky v1.3.0, whose message tools
# return CSV text (header: MsgID,UserID,UserName,RealName,Channel,ThreadTs,
# Text,Time,...). MsgID is the Slack message timestamp.
_CSV_MESSAGE_COLUMNS = {"MsgID", "UserID", "Text"}


def _messages_from_csv(text: str) -> list[dict[str, Any]] | None:
    """Parse the vendor's CSV message payload into Slack-shaped dicts.

    Returns None when the text isn't recognizably that CSV, so callers can
    fall through to other formats.
    """
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not _CSV_MESSAGE_COLUMNS.issubset(rows[0].keys()):
        return None
    return [
        {
            "ts": row.get("MsgID", ""),
            "user": row.get("UserName") or row.get("UserID") or "",
            "text": row.get("Text", ""),
            "thread_ts": row.get("ThreadTs", ""),
        }
        for row in rows
    ]


def extract_messages(result: CallToolResult) -> list[dict[str, Any]]:
    """Normalize a vendor message-tool result to a list of message dicts
    with Slack-style keys (``ts``, ``user``, ``text``)."""
    return messages_from_payload(extract_payload(result))


def messages_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Message list from any payload format seen across vendors: korotovsky's
    CSV text, a bare JSON list, or a Slack-API-shaped {"messages": [...]}
    object. Anything else is a contract break worth surfacing.
    """
    if isinstance(payload, str):
        messages = _messages_from_csv(payload)
        if messages is not None:
            return messages
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return payload["messages"]
    if isinstance(payload, list):
        return payload
    raise ToolError(
        "Unexpected message payload from the vendor server; "
        f"got {type(payload).__name__}, expected CSV text or a message list."
    )


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
