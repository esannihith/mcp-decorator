"""slack_channel_health_report: channel activity metrics from message history.

One vendor call (conversations_history) plus local computation. The vendor
exposes no member-list tool, so metrics describe *posting* activity — who
writes and how fast the conversation moves — not channel membership.

The metric math lives in pure functions over plain message dicts so it can be
unit-tested without a vendor or network.
"""

from collections import Counter
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from slack_mcp_wrapper.upstream import Vendor, extract_payload


def compute_channel_metrics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Activity metrics over Slack message dicts (need ``user`` and ``ts``).

    Messages missing an author (channel join notices, some bot events) are
    counted toward volume/timing but excluded from per-user rankings.
    """
    timestamps = sorted(float(m["ts"]) for m in messages if m.get("ts"))
    per_user = Counter(str(m["user"]) for m in messages if m.get("user"))

    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    ranked = per_user.most_common()

    return {
        "message_count": len(messages),
        "participant_count": len(per_user),
        "messages_per_user": dict(ranked),
        "most_active": (
            [{"user": u, "messages": c} for u, c in ranked if c == ranked[0][1]]
            if ranked
            else []
        ),
        "least_active": (
            [{"user": u, "messages": c} for u, c in ranked if c == ranked[-1][1]]
            if ranked
            else []
        ),
        "avg_response_gap_seconds": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "span_seconds": round(timestamps[-1] - timestamps[0], 1) if len(timestamps) > 1 else 0,
    }


def extract_messages(payload: Any) -> list[dict[str, Any]]:
    """Normalize a vendor conversations_history payload to a message list.

    Accepts either a bare list of messages or a Slack-API-shaped object with
    a ``messages`` key; anything else is a contract break worth surfacing.
    """
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return payload["messages"]
    if isinstance(payload, list):
        return payload
    raise ToolError(
        "Unexpected conversations_history payload from the vendor server; "
        f"got {type(payload).__name__}, expected a message list."
    )


def register(mcp: FastMCP, vendor: Vendor) -> None:
    @mcp.tool
    async def slack_channel_health_report(channel_id: str, limit: str = "100") -> dict[str, Any]:
        """Activity report for one Slack channel: message volume, messages per
        participant, most/least active posters, and average response gap.
        Computed from recent history (limit = message count like "100", or a
        period like "1d"/"1w" as supported by the vendor). Use
        slack_channels_list first to resolve the channel ID."""
        result = await vendor.call_tool(
            "conversations_history",
            {"channel_id": channel_id, "limit": limit},
        )
        messages = extract_messages(extract_payload(result))
        return {"channel_id": channel_id, **compute_channel_metrics(messages)}
