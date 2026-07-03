"""The single integration point with the upstream vendor MCP server.

Everything that knows *how* to reach the vendor lives here: the proxy provider
that forwards passthrough tools, and the client used by composite tools for
their internal vendor calls. Both are built from the same Settings, so
swapping the upstream (e.g. to Slack's official mcp.slack.com server) touches
only .env and the allowlist in overrides.py.
"""

from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy
from fastmcp.server import create_proxy

from slack_mcp_wrapper.config import Settings


def make_vendor_client(settings: Settings) -> Client:
    """A fresh MCP client session to the vendor server.

    Composite tools open one of these per invocation rather than sharing the
    proxy's session, keeping their calls isolated from passthrough traffic.
    """
    return Client(
        settings.vendor_slack_mcp_url,
        auth=settings.vendor_api_key,
    )


def make_vendor_proxy(settings: Settings) -> FastMCPProxy:
    """The proxy that sources the vendor's tools for forwarding.

    Connection is lazy: the vendor is first contacted when an AI client
    initializes against the wrapper, so the wrapper can boot without the
    vendor being up.
    """
    return create_proxy(make_vendor_client(settings), name="slack-vendor")
