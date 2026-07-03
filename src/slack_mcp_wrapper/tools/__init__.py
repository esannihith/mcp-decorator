"""Local composite tools added by the wrapper on top of the vendor's."""

from fastmcp import FastMCP

from slack_mcp_wrapper.tools import health_report, thread_digest
from slack_mcp_wrapper.upstream import Vendor


def register_all(mcp: FastMCP, vendor: Vendor) -> None:
    health_report.register(mcp, vendor)
    thread_digest.register(mcp, vendor)
