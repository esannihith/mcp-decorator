"""slack_thread_digest: summarize a Slack thread with the *client's* LLM.

Pulls the thread via the vendor's conversations_replies tool, then asks the
connected client's own model for a summary through MCP sampling
(sampling/createMessage). The wrapper holds no LLM credentials and pays for
no inference.

Sampling requires client support. When unavailable (or the request fails),
the tool degrades instead of erroring: it returns the assembled transcript
with a note, so the calling model can summarize it directly.
"""

from typing import Any

from fastmcp import Context, FastMCP

from slack_mcp_wrapper.tools.health_report import parse_ts
from slack_mcp_wrapper.upstream import Vendor, extract_messages

# Keeps the sampling prompt bounded on very long threads; the tail of a thread
# carries the resolution, so we trim from the front.
MAX_TRANSCRIPT_CHARS = 12_000

SYSTEM_PROMPT = (
    "You summarize Slack threads. Reply with a short digest: 2-4 sentences on "
    "what was discussed and decided, then any open action items as bullets. "
    "Attribute decisions and action items to usernames when clear."
)


def build_transcript(messages: list[dict[str, Any]]) -> str:
    """Flatten thread messages to 'user: text' lines, oldest first."""
    ordered = sorted(messages, key=lambda m: parse_ts(m.get("ts")) or 0.0)
    lines = [
        f"{m.get('user', 'unknown')}: {m.get('text', '')}".strip()
        for m in ordered
        if m.get("text")
    ]
    transcript = "\n".join(lines)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = "[earlier messages trimmed]\n" + transcript[-MAX_TRANSCRIPT_CHARS:]
    return transcript


def register(mcp: FastMCP, vendor: Vendor) -> None:
    @mcp.tool
    async def slack_thread_digest(channel_id: str, thread_ts: str, ctx: Context) -> dict[str, Any]:
        """Short digest of one Slack thread: what was discussed, decisions,
        and action items. Fetches the full thread and summarizes it with the
        connected client's own model via MCP sampling — prefer this over
        slack_conversations_replies when only the gist is needed. thread_ts is
        the timestamp of the thread's first message."""
        result = await vendor.call_tool(
            "conversations_replies",
            {"channel_id": channel_id, "thread_ts": thread_ts},
        )
        messages = extract_messages(result)
        transcript = build_transcript(messages)

        if not transcript:
            return {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "message_count": len(messages),
                "digest": None,
                "note": "Thread contains no text messages to summarize.",
            }

        try:
            sampled = await ctx.sample(
                f"Summarize this Slack thread:\n\n{transcript}",
                system_prompt=SYSTEM_PROMPT,
                max_tokens=500,
            )
            return {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "message_count": len(messages),
                "digest": sampled.text,
            }
        except Exception as exc:
            # Most commonly: the client doesn't implement MCP sampling.
            # Degrade to raw material rather than failing the call.
            return {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "message_count": len(messages),
                "digest": None,
                "note": (
                    "Client-side summarization via MCP sampling was unavailable "
                    f"({type(exc).__name__}). Raw transcript returned; summarize it directly."
                ),
                "transcript": transcript,
            }
