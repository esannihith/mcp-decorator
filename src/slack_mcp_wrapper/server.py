"""Wrapper server assembly and entrypoint.

The wrapper is a plain FastMCP server composed of:

* the vendor proxy, mounted under the ``slack`` namespace with description
  overrides applied (upstream.py + overrides.py);
* local composite tools (tools/), which are ordinary @mcp.tool functions;
* an allowlist so only the curated tool set is visible to clients.
"""

from fastmcp import FastMCP
from fastmcp.server.transforms import ToolTransform

from slack_mcp_wrapper import tools
from slack_mcp_wrapper.config import Settings, load_settings
from slack_mcp_wrapper.overrides import NAMESPACE, TOOL_OVERRIDES, allowed_tools
from slack_mcp_wrapper.upstream import Vendor


def build_server(settings: Settings, vendor: Vendor | None = None) -> FastMCP:
    """Assemble the wrapper.

    ``vendor`` lets tests substitute an in-memory upstream; production
    resolves it from settings.
    """
    vendor = vendor or Vendor.from_settings(settings)

    mcp = FastMCP(
        "slack-mcp-wrapper",
        instructions=(
            "Slack access plus composite reporting tools. Channel IDs come "
            "from slack_channels_list; thread summaries from slack_thread_digest."
        ),
    )

    proxy = vendor.proxy()
    # Overrides use raw vendor names; the namespace is applied by mount below.
    proxy.add_transform(ToolTransform(TOOL_OVERRIDES))
    mcp.mount(proxy, namespace=NAMESPACE)

    tools.register_all(mcp, vendor)

    # Allowlist, not blocklist: vendor tools we didn't curate stay hidden even
    # if the vendor adds or renames tools later. Verified live: only=True also
    # hides the vendor's resources/prompts, so the wrapper's whole surface is
    # exactly these tools — intended, as this project's scope is tools-only.
    mcp.enable(names=allowed_tools(), only=True, components={"tool"})

    return mcp


def main() -> None:
    settings = load_settings()
    server = build_server(settings)
    server.run(
        transport="http",
        host=settings.wrapper_host,
        port=settings.wrapper_port,
    )


if __name__ == "__main__":
    main()
