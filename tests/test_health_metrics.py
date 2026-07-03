"""Unit tests for the pure metric computation and vendor payload parsing."""

import pytest
from fastmcp.exceptions import ToolError

from slack_mcp_wrapper.tools.health_report import compute_channel_metrics
from slack_mcp_wrapper.upstream import messages_from_payload


def msg(user: str | None, ts: float, text: str = "hi") -> dict:
    m: dict = {"ts": f"{ts:.6f}", "text": text}
    if user:
        m["user"] = user
    return m


def test_basic_metrics():
    messages = [
        msg("alice", 1000.0),
        msg("bob", 1060.0),
        msg("alice", 1180.0),
        msg("carol", 1240.0),
    ]
    report = compute_channel_metrics(messages)
    assert report["message_count"] == 4
    assert report["participant_count"] == 3
    assert report["messages_per_user"] == {"alice": 2, "bob": 1, "carol": 1}
    assert report["most_active"] == [{"user": "alice", "messages": 2}]
    # bob and carol tie for least active
    assert {e["user"] for e in report["least_active"]} == {"bob", "carol"}
    assert report["avg_response_gap_seconds"] == 80.0  # (60 + 120 + 60) / 3
    assert report["span_seconds"] == 240.0


def test_empty_channel():
    report = compute_channel_metrics([])
    assert report["message_count"] == 0
    assert report["participant_count"] == 0
    assert report["most_active"] == []
    assert report["least_active"] == []
    assert report["avg_response_gap_seconds"] is None
    assert report["span_seconds"] == 0


def test_single_message():
    report = compute_channel_metrics([msg("alice", 1000.0)])
    assert report["message_count"] == 1
    assert report["avg_response_gap_seconds"] is None
    assert report["span_seconds"] == 0


def test_authorless_messages_count_for_volume_not_ranking():
    messages = [msg("alice", 1000.0), msg(None, 1060.0)]
    report = compute_channel_metrics(messages)
    assert report["message_count"] == 2
    assert report["participant_count"] == 1
    assert report["messages_per_user"] == {"alice": 1}


def test_messages_from_payload_accepts_slack_shape_and_bare_list():
    messages = [msg("alice", 1000.0)]
    assert messages_from_payload({"messages": messages, "ok": True}) == messages
    assert messages_from_payload(messages) == messages


# Header and row shape as returned live by korotovsky v1.3.0.
VENDOR_CSV = (
    "MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Permalink,"
    "Reactions,BotName,FileCount,AttachmentIDs,HasMedia,Cursor\n"
    "1783104269.645549,U0BEM157DB9,demo_app,Demo App,C0BEWGH5K5F,,"
    "Kicking off the review.,2026-07-03T18:44:29Z,,,Demo App,0,,false,\n"
    '1783103660.831099,U0BENBLGU11,esanni04,e sanni,C0BEWGH5K5F,,"Hi, Demo App",'
    "2026-07-03T18:34:20Z,,,,0,,false,\n"
)


def test_messages_from_payload_parses_vendor_csv():
    messages = messages_from_payload(VENDOR_CSV)
    assert len(messages) == 2
    assert messages[0] == {
        "ts": "1783104269.645549",
        "user": "demo_app",
        "text": "Kicking off the review.",
        "thread_ts": "",
    }
    # Quoted field with a comma survives CSV parsing.
    assert messages[1]["text"] == "Hi, Demo App"
    # And the metrics pipeline accepts the normalized shape.
    report = compute_channel_metrics(messages)
    assert report["participant_count"] == 2


def test_messages_from_payload_rejects_garbage():
    with pytest.raises(ToolError):
        messages_from_payload("not,a,message\nlist,of,any,kind")
    with pytest.raises(ToolError):
        messages_from_payload(42)
