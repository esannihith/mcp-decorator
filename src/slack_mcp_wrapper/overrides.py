"""What we forward from the vendor and how we present it — data, not logic.

Three concerns are pinned here:

* NAMESPACE        — vendor tools are exposed as ``slack_<vendor name>``.
* TOOL_OVERRIDES   — rewritten descriptions for forwarded tools. The rule:
                     override the description, never the behavior. Schemas
                     and semantics stay exactly the vendor's.
* allowed_tools()  — the wrapper's public tool allowlist. Vendor tools not
                     listed (usergroups, saved items, unreads, ...) are
                     hidden, and a vendor adding new tools won't silently
                     grow this wrapper's surface.

Keys in TOOL_OVERRIDES are the vendor's *raw* tool names (the namespace is
applied afterwards, when the proxy is mounted). If the vendor renames a tool,
this file and this file only should need the update — same for swapping to
Slack's official server, whose tool names differ.
"""

from fastmcp.tools.tool_transform import ToolTransformConfig

NAMESPACE = "slack"

# Vendor: korotovsky/slack-mcp-server.
TOOL_OVERRIDES: dict[str, ToolTransformConfig] = {
    "channels_list": ToolTransformConfig(
        description=(
            "List channels in the Slack workspace (ID, name, topic, purpose, "
            "member count). Use this first to resolve a human channel name to "
            "the channel ID required by every other Slack tool."
        ),
    ),
    "conversations_history": ToolTransformConfig(
        description=(
            "Fetch recent top-level messages from one channel or DM by channel "
            "ID. Returns message text, author, and timestamp; thread replies "
            "are NOT included — use slack_conversations_replies for those."
        ),
    ),
    "conversations_replies": ToolTransformConfig(
        description=(
            "Fetch the full message thread for a given channel ID and thread "
            "timestamp (thread_ts). Prefer slack_thread_digest when only a "
            "summary of the thread is needed."
        ),
    ),
    "conversations_add_message": ToolTransformConfig(
        description=(
            "Post a message to a channel or thread. The vendor server keeps "
            "posting disabled unless explicitly enabled at its side; expect an "
            "error if it is not."
        ),
    ),
}

# Local composite tools, registered on the wrapper itself (see tools/).
COMPOSITE_TOOLS: set[str] = {
    "slack_channel_health_report",
    "slack_thread_digest",
}


def allowed_tools() -> set[str]:
    """Every tool name the wrapper exposes, post-namespacing."""
    return {f"{NAMESPACE}_{name}" for name in TOOL_OVERRIDES} | COMPOSITE_TOOLS
