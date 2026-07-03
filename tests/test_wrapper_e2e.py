"""End-to-end wrapper tests over an in-memory fake vendor.

FastMCP clients can connect to a FastMCP instance directly in memory, so the
full wire path — proxy provider, rename/description transforms, namespace,
allowlist, composite tools, sampling — is exercised with no network and no
credentials. Payload shapes mirror what the live vendor (korotovsky v1.3.0)
actually returns: CSV text.
"""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from slack_mcp_wrapper.config import Settings
from slack_mcp_wrapper.server import build_server
from slack_mcp_wrapper.upstream import Vendor

CSV_HEADER = (
    "MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Permalink,"
    "Reactions,BotName,FileCount,AttachmentIDs,HasMedia,Cursor"
)


def make_fake_vendor() -> FastMCP:
    vendor = FastMCP("fake-vendor")

    @vendor.tool
    def channels_list(channel_types: str = "") -> str:
        """vendor original description"""
        return "ID,Name,Topic,Purpose,MemberCount,Cursor\nC123,#general,,team chat,2,"

    @vendor.tool
    def conversations_history(channel_id: str, limit: str = "100") -> str:
        return "\n".join(
            [
                CSV_HEADER,
                f"1000.000001,U1,alice,Alice,{channel_id},,hello,2026-01-01T00:00:00Z,,,,0,,false,",
                f"1060.000001,U2,bob,Bob,{channel_id},,hi,2026-01-01T00:01:00Z,,,,0,,false,",
                f"1180.000001,U1,alice,Alice,{channel_id},,update?,2026-01-01T00:03:00Z,,,,0,,false,",
            ]
        )

    @vendor.tool
    def conversations_replies(channel_id: str, thread_ts: str) -> str:
        return "\n".join(
            [
                CSV_HEADER,
                f"{thread_ts},U1,alice,Alice,{channel_id},{thread_ts},prod broke,_,,,,0,,false,",
                f"1010.000001,U2,bob,Bob,{channel_id},{thread_ts},rolling back,_,,,,0,,false,",
            ]
        )

    @vendor.tool
    def conversations_add_message(channel_id: str, text: str, thread_ts: str = "") -> str:
        return f"posted to {channel_id}: {text}"

    @vendor.tool
    def usergroups_list() -> str:
        """Not on the wrapper allowlist; must stay hidden."""
        return "should never be reachable"

    return vendor


@pytest.fixture
def wrapper() -> FastMCP:
    return build_server(Settings(), vendor=Vendor(target=make_fake_vendor()))


async def test_tool_surface_is_exactly_the_curated_set(wrapper):
    async with Client(wrapper) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert set(tools) == {
            "slack_channels_list",
            "slack_conversations_history",
            "slack_conversations_replies",
            "slack_post_message",
            "slack_channel_health_report",
            "slack_thread_digest",
        }
        # Descriptions are ours, not the vendor's.
        assert "vendor original" not in tools["slack_channels_list"].description


async def test_renamed_tool_routes_to_vendor_original(wrapper):
    async with Client(wrapper) as client:
        result = await client.call_tool(
            "slack_post_message", {"channel_id": "C123", "text": "ship it"}
        )
        assert "posted to C123: ship it" in result.content[0].text


async def test_non_allowlisted_vendor_tool_is_blocked(wrapper):
    async with Client(wrapper) as client:
        with pytest.raises(ToolError):
            await client.call_tool("slack_usergroups_list", {})


async def test_health_report_over_vendor_csv(wrapper):
    async with Client(wrapper) as client:
        result = await client.call_tool(
            "slack_channel_health_report", {"channel_id": "C123"}
        )
        report = result.data
        assert report["message_count"] == 3
        assert report["messages_per_user"] == {"alice": 2, "bob": 1}
        assert report["avg_response_gap_seconds"] == 90.0


async def test_thread_digest_uses_client_sampling(wrapper):
    async def fake_llm(messages, params, context):
        return "digest: prod broke, bob rolled back"

    async with Client(wrapper, sampling_handler=fake_llm) as client:
        result = await client.call_tool(
            "slack_thread_digest", {"channel_id": "C123", "thread_ts": "1000.000001"}
        )
        assert result.data["digest"] == "digest: prod broke, bob rolled back"


async def test_thread_digest_degrades_without_sampling(wrapper):
    async with Client(wrapper) as client:
        result = await client.call_tool(
            "slack_thread_digest", {"channel_id": "C123", "thread_ts": "1000.000001"}
        )
        assert result.data["digest"] is None
        assert "alice: prod broke" in result.data["transcript"]
