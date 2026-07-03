# mcp-decorator

[![CI](https://github.com/esannihith/mcp-decorator/actions/workflows/ci.yml/badge.svg)](https://github.com/esannihith/mcp-decorator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![FastMCP 3.x](https://img.shields.io/badge/FastMCP-3.x-green)](https://gofastmcp.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](#license)

**Wrap a vendor's MCP server. Forward what works. Add what's missing.**

This repo is a working reference implementation of the *MCP decorator pattern* — an MCP server that sits in front of another vendor-provided MCP server, forwards a curated subset of its tools unchanged, and adds new composite tools the vendor can't offer. The reference implementation (the `slack_mcp_wrapper` package) is demonstrated and live-tested against a Slack MCP server, but the pattern applies to any vendor server (Jira, GitHub, Notion, ...).

---

## Table of contents

- [The idea: wrapping a vendor MCP server](#the-idea-wrapping-a-vendor-mcp-server)
- [What this repo demonstrates](#what-this-repo-demonstrates)
- [Architecture](#architecture)
- [Tools](#tools)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Testing](#testing)
- [Design decisions](#design-decisions)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## The idea: wrapping a vendor MCP server

Vendors ship MCP servers whose tools map roughly 1:1 to their REST APIs. Useful — but fixed. Two things a vendor server can never do for you:

1. **Combine its own tools into something new.** Slack has no "channel health report" endpoint; it has separate history and channel-listing endpoints. Only you can build the combination.
2. **Use the caller's intelligence.** Summarizing a thread needs an LLM, and the best-placed LLM is the one already driving the conversation. MCP's **sampling** primitive lets a server ask the *client's* model to generate text — no API key on the server.

The wrapper pattern solves both without forking the vendor. Architecturally it's the **Decorator pattern applied to MCP**:

- **To the AI client**, the wrapper is one ordinary MCP server — the client never knows a vendor is behind it.
- **To the vendor server**, the wrapper is one ordinary MCP client.
- In between: forward the tools that are fine as-is (optionally with better descriptions), hide the ones that aren't wanted, and add new tools that orchestrate vendor calls or request client-side inference.

This is **not** a gateway or aggregator — no multi-backend routing, no load balancing. Exactly one upstream, augmented.

Three rules keep a wrapper honest:

| Rule | Meaning |
|---|---|
| Override descriptions, never behavior | A forwarded tool must do exactly what the vendor's does, even if we re-describe it |
| Allowlist, don't blocklist | Vendor tool sets drift; unknown tools stay hidden by default |
| One integration point | Everything vendor-specific lives in two files, so swapping the upstream is contained |

## What this repo demonstrates

The pattern above, implemented with [FastMCP 3.x](https://gofastmcp.com/) and **live-verified end to end** against a real Slack workspace through [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server):

- ✅ Passthrough with curated descriptions (4 vendor tools, allowlisted, `slack_` namespaced — one exposed under our own name while the vendor does the work)
- ✅ A composite tool combining a vendor call with local computation (`slack_channel_health_report`)
- ✅ A composite tool using **MCP sampling** — the connected client's own LLM does the summarization (`slack_thread_digest`), with graceful degradation for clients that don't support sampling
- ✅ Vendor contract survival: the vendor's real tool set differs from its own README and its payloads turned out to be CSV, not JSON — the allowlist and the isolated parser absorbed both discoveries without touching tool code
- ✅ No hand-rolled registry or dispatcher: FastMCP 3's providers (`create_proxy`), transforms (`Namespace`, `ToolTransform`), and visibility allowlist (`enable(only=True)`) do all the merging and routing

## Architecture

```mermaid
flowchart TB
    Client["AI client + its LLM<br/>(IDE assistant, Claude Desktop, ...)"]
    Vendor["Vendor Slack MCP server<br/>(korotovsky/slack-mcp-server)"]
    SlackAPI["Slack Web API"]

    subgraph Wrapper["mcp-decorator (FastMCP 3.x)"]
        Proxy["Proxy provider<br/>sources tools from the vendor server"]
        Transforms["Transforms<br/>slack_ namespace · description overrides · allowlist"]
        Local["Composite tools<br/>slack_channel_health_report · slack_thread_digest"]
    end

    Client -- "MCP: initialize, tools/list, tools/call" --> Wrapper
    Wrapper -- "MCP sampling: 'summarize this thread'" --> Client
    Proxy --> Transforms
    Proxy -- "MCP client session (lazy)" --> Vendor
    Local -- "internal MCP calls" --> Vendor
    Vendor -- "Slack Web API calls" --> SlackAPI
```

Key properties, all verified live:

- **Single surface.** The client sees exactly 6 tools; vendor origin is invisible. Vendor tools outside the allowlist — and the vendor's resources/prompts — are hidden (this project's scope is tools-only).
- **Lazy upstream connection.** The wrapper boots and serves with the vendor down; the vendor session is opened on first use, and FastMCP caches upstream tool lists (~300s TTL) between calls.
- **Errors propagate honestly.** A vendor failure (e.g. `channel_not_found`) reaches the client with the vendor's original message.
- **Credential isolation.** The Slack token lives in the *vendor's* process. The wrapper holds no Slack credentials and no LLM key — inference happens client-side via sampling.

## Tools

### Passthrough (vendor behavior, our descriptions)

| Tool | What it does |
|---|---|
| `slack_channels_list` | List workspace channels; resolves names → channel IDs |
| `slack_conversations_history` | Recent top-level messages of a channel |
| `slack_conversations_replies` | Full message thread by `channel_id` + `thread_ts` |
| `slack_post_message` | Post a message or thread reply (vendor keeps this disabled unless `SLACK_MCP_ADD_MESSAGE_TOOL` is set) |

`slack_post_message` also demonstrates **renaming**: it is the vendor's `conversations_add_message` surfaced under our own name — the transform maps calls back, the vendor still does the work (`ToolTransformConfig(name=...)` in `overrides.py`).

### Composite (added by this wrapper)

**`slack_channel_health_report(channel_id, limit)`** — one vendor history call + local metrics: messages per participant, most/least active posters, average response gap, time span. (The vendor exposes no member-list tool, so metrics describe posting activity, not membership.)

**`slack_thread_digest(channel_id, thread_ts)`** — fetches the thread, then requests a summary **from the connected client's own model via MCP sampling**. Clients without sampling support get the assembled transcript with a note instead of an error.

> **Vendor payload note:** korotovsky v1.3.0 returns message data as CSV text (`MsgID,UserID,UserName,...,Text`; `MsgID` is the Slack timestamp). Parsing lives in `upstream.py` (`messages_from_payload`), which also accepts JSON shapes for other upstreams.

## Getting started

### Prerequisites

- Python 3.11+ and Node.js (the vendor server ships as an npm package wrapping its Go binary)
- A Slack workspace you control and a Slack app with a bot token (`xoxb-...`)
- An MCP client; one that supports **sampling** gets the full `slack_thread_digest` experience (others get the degraded path)

Bot token scopes (the vendor lists all conversation types at boot, so all read scopes are required): `channels:read`, `channels:history`, `groups:read`, `im:read`, `mpim:read`, `users:read` — plus `chat:write` if posting is enabled. Reinstall the app after adding scopes and `/invite` the bot to the channels it should read.

### 1. Run the vendor Slack MCP server

```powershell
$env:SLACK_MCP_XOXB_TOKEN = "xoxb-your-bot-token"
$env:SLACK_MCP_ADD_MESSAGE_TOOL = "true"   # optional: enables posting
npx -y slack-mcp-server@latest --transport sse   # serves on 127.0.0.1:13080/sse
```

### 2. Install and run the wrapper

```powershell
cp .env.example .env    # defaults match the vendor command above
pip install -e ".[dev]"
python -m slack_mcp_wrapper.server
```

### 3. Connect a client

```json
{
  "mcpServers": {
    "slack-wrapper": { "url": "http://127.0.0.1:8080/mcp" }
  }
}
```

Then ask your assistant e.g. *"give me a health report for #general"* or *"digest the thread about the deploy"*.

## Configuration

All settings are environment variables (or `.env`), read by `config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `VENDOR_SLACK_MCP_URL` | `http://127.0.0.1:13080/sse` | Upstream MCP endpoint, including transport path |
| `VENDOR_API_KEY` | *(unset)* | Bearer key, only if the vendor runs with `SLACK_MCP_API_KEY` |
| `WRAPPER_HOST` / `WRAPPER_PORT` | `127.0.0.1` / `8080` | Where the wrapper listens |

The Slack token is **not** wrapper configuration — it belongs to the vendor process (`SLACK_MCP_XOXB_TOKEN`).

## Testing

**Unit tests** (no network, no credentials):

```powershell
python -m pytest tests/ -q
```

Two layers, 15 tests: unit tests for the health-report metric math (ties, single poster, malformed timestamps) and the vendor CSV/JSON payload parsing (samples captured from the live vendor); plus wrapper end-to-end tests that connect a FastMCP client to the assembled server **in memory** against a fake vendor — tool surface, rename routing, allowlist blocking, composite tools, and both sampling paths. The same suite runs in CI on every push.

**Live verification** (what "works" means here): with vendor + wrapper running, connect [MCP Inspector](https://github.com/modelcontextprotocol/inspector) or an IDE and confirm `tools/list` shows exactly the 6 tools, then exercise each `tools/call` against a real channel and thread. Inspector supports sampling, so `slack_thread_digest` runs end to end. This full pass — including posting into a thread and both digest paths — has been executed against a real workspace.

**Contract check:** run `tools/list` directly against the vendor and diff against the allowlist in `overrides.py` — vendor servers rename and change tools without notice (observed in practice).

## Design decisions

- **FastMCP 3 primitives over hand-rolled plumbing.** An earlier design planned a custom tool registry and call dispatcher; `create_proxy` + transforms + `enable(only=True)` made both redundant.
- **Sampling over a server-side LLM key.** The client's model is already paid for and in context; the wrapper stays credential-free. This is deliberate architecture, not a shortcut.
- **Vendor knowledge is quarantined.** `upstream.py` (connection + payload parsing) and `overrides.py` (names, descriptions, allowlist) are the only files that know Slack or korotovsky specifics. Swapping the upstream — including to **Slack's official MCP server** (`https://mcp.slack.com/mcp`; streamable HTTP only; requires a directory-published or internal app, per-user confidential OAuth, and workspace-admin approval) — is a config change plus an allowlist update, not a rewrite.
- **Composite tools open a fresh vendor session per call.** Isolation over connection reuse at this scale.

## Limitations

- **One upstream, no fallback.** Vendor down ⇒ passthrough tools drop from `tools/list` and composite tools fail at call time (the wrapper itself keeps serving).
- **Rate limits compound.** Composite tools multiply vendor calls; against Slack's official server, its per-tool rate tiers apply on top.
- **Single service-account token.** Actions aren't attributed to individual end users. The production path is per-user OAuth — which the official Slack upstream enforces by design; the open-source vendor used here has no OAuth flow at all (static tokens only).
- **Sampling depends on the client.** No sampling support ⇒ `slack_thread_digest` returns the transcript instead of a digest.

## Roadmap

- [ ] Swap the upstream to Slack's official remote MCP server (brings per-user OAuth handled by the vendor side)
- [ ] `slack_triage_and_notify` — cross-system composite (Slack → Notion)
- [ ] Grow from single-vendor wrapper to multi-vendor gateway (aggregation, namespacing, load balancing)

## Acknowledgements

- [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server) — the vendor server wrapped here
- [FastMCP](https://gofastmcp.com/) — providers, transforms, proxying, sampling
- [Slack's official MCP server](https://docs.slack.dev/ai/slack-mcp-server/) — the production upstream this wrapper is swap-ready for
- [`metatool-ai/metamcp`](https://github.com/metatool-ai/metamcp) — reference for the gateway pattern this may grow into

## License

MIT
