"""Unit tests for the pure metric computation behind slack_channel_health_report."""

import pytest
from fastmcp.exceptions import ToolError

from slack_mcp_wrapper.tools.health_report import compute_channel_metrics, extract_messages


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


def test_extract_messages_accepts_slack_shape_and_bare_list():
    messages = [msg("alice", 1000.0)]
    assert extract_messages({"messages": messages, "ok": True}) == messages
    assert extract_messages(messages) == messages


def test_extract_messages_rejects_garbage():
    with pytest.raises(ToolError):
        extract_messages("not a message list")
