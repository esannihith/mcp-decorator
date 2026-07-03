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

from slack_mcp_wrapper.upstream import Vendor, extract_messages


def parse_ts(value: Any) -> float | None:
    """Slack timestamp as float, or None for missing/malformed values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_channel_metrics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Activity metrics over Slack message dicts (need ``user`` and ``ts``).

    Messages missing an author (channel join notices, some bot events) are
    counted toward volume/timing but excluded from per-user rankings, and
    malformed timestamps are skipped rather than crashing the report.
    """
    timestamps = sorted(ts for m in messages if (ts := parse_ts(m.get("ts"))) is not None)
    per_user = Counter(str(m["user"]) for m in messages if m.get("user"))

    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    ranked = per_user.most_common()
    top = ranked[0][1] if ranked else 0
    bottom = ranked[-1][1] if ranked else 0

    return {
        "message_count": len(messages),
        "participant_count": len(per_user),
        "messages_per_user": dict(ranked),
        "most_active": [{"user": u, "messages": c} for u, c in ranked if c == top],
        # Only meaningful when someone is actually behind the pack; when all
        # participants tie (or there's one poster), an empty list beats
        # repeating most_active.
        "least_active": (
            [{"user": u, "messages": c} for u, c in ranked if c == bottom]
            if bottom < top
            else []
        ),
        "avg_response_gap_seconds": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "span_seconds": round(timestamps[-1] - timestamps[0], 1) if len(timestamps) > 1 else 0,
    }


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
        messages = extract_messages(result)
        return {"channel_id": channel_id, **compute_channel_metrics(messages)}
