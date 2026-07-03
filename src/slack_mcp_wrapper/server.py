"""Wrapper server assembly and entrypoint.

Phase 1 scaffold: a bare FastMCP server that boots and serves nothing but a
health-check tool. The vendor proxy provider and composite tools are attached
in later phases.
"""

from fastmcp import FastMCP

from slack_mcp_wrapper.config import Settings, load_settings


def build_server(settings: Settings) -> FastMCP:
    mcp = FastMCP("slack-mcp-wrapper")

    @mcp.tool
    def wrapper_ping() -> str:
        """Health check for the wrapper itself; does not touch the vendor server."""
        return "slack-mcp-wrapper is up"

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
