# slack-mcp-wrapper

An MCP server that wraps an existing Slack MCP server and adds custom orchestration tools on top of it, without modifying or forking the underlying server.

This is **not** a gateway or aggregator. It doesn't route between multiple different backends or load-balance replicas. It wraps exactly one upstream MCP server and augments it — architecturally, this is the Decorator pattern applied to MCP: forward what you don't need to change, add new behavior where you do.

> **Status:** work in progress. See [Implementation status](#implementation-status) for what's built vs. planned.

## Why this exists

Vendor-provided MCP servers (Slack, Jira, GitHub, etc.) expose a fixed set of tools that map roughly 1:1 to their REST API. They're useful, but two things they can't do on their own:

- **Combine their own tools into something new.** Slack's API has no "give me a channel health report" endpoint — it has separate endpoints for message history and member lists. Nobody upstream can build the combination for you.
- **Call out to systems the vendor doesn't know exist.** Slack's API can't summarize a thread using an LLM, because that's not a Slack capability — it's yours to add.

This wrapper sits between an AI client and a vendor Slack MCP server, forwards the tools that don't need changing, and adds new tools that do one or both of the above.

## Architecture

```mermaid
flowchart TB
    Client["AI client<br/>(Claude Desktop, Cursor, etc.)"]
    Wrapper["slack-mcp-wrapper<br/>(this project)"]
    Vendor["Vendor Slack MCP server<br/>(korotovsky/slack-mcp-server)"]
    SlackAPI["Slack Web API"]

    subgraph Wrapper_internals["Inside the wrapper"]
        Registry["Tool registry<br/>merges vendor + local tool lists"]
        Dispatch["Call dispatcher<br/>routes tools/call by name"]
        Local["Local composite tools<br/>(health report, thread digest)"]
        LLM["Anthropic API<br/>(for summarization)"]
    end

    Client -- "MCP: initialize, tools/list, tools/call" --> Wrapper
    Wrapper --> Registry
    Wrapper --> Dispatch
    Dispatch -- "vendor tool call" --> Vendor
    Dispatch -- "custom tool call" --> Local
    Local -- "HTTP request" --> LLM
    Vendor -- "Slack Web API calls" --> SlackAPI
```

The wrapper plays two roles at once:

- **To the AI client, it is an MCP server.** The client only ever configures one connection — to this wrapper — and never talks to the vendor server or Slack directly.
- **To the vendor Slack MCP server, it is an MCP client.** It opens its own MCP session to the vendor server, the same way any AI client would, and calls `tools/list` / `tools/call` on it internally.

### Why the vendor server runs as a standalone process, not a subprocess

The wrapper connects to the vendor Slack MCP server over network transport (SSE/streamable HTTP) rather than spawning it as a stdio subprocess, even though both run on localhost during development. This keeps the client code identical whether the vendor server is running next to the wrapper or is Slack's actual official remote server (`mcp.slack.com`) — swapping the vendor endpoint later is a config change, not a rewrite.

## Tools

### Passthrough tools (forwarded from the vendor, unchanged behavior)

| Tool | Source | Notes |
|---|---|---|
| `slack__list_channels` | Vendor | Description may be tightened for clearer model use; behavior untouched |
| `slack__post_message` | Vendor | Disabled by default in the vendor server unless explicitly enabled |
| `slack__conversations_history` | Vendor | Used internally by composite tools below |
| `slack__conversations_replies` | Vendor | Used internally by `slack_thread_digest` |

### Composite tools (new capabilities added by this wrapper)

**`slack_channel_health_report`**
Combines the vendor's message history and member list tools into a single report: messages per member, average response gap, least active participants. Pure orchestration — no external HTTP call, just two vendor tool calls combined and computed locally.

**`slack_thread_digest`**
Pulls a thread's full text via the vendor's `conversations_replies` tool, then makes one HTTP call to the Anthropic API to summarize it, and returns a short digest. This is a capability the vendor's API has no equivalent for.

### Roadmap tools (not yet built)

**`slack_triage_and_notify`** — on hold. Would extract action items from a thread via an LLM call and post them to a second external system (e.g. Notion). Deferred to keep the current scope to a single vendor with no second external write-target dependency.

## Tool description overrides

Tool descriptions forwarded from the vendor are not always passed through verbatim. Descriptions may be rewritten to be more decision-relevant for a model choosing between tools (what it returns, when to prefer it over a similar tool). The rule followed throughout: **override the description, never the behavior.** If a description doesn't match what the underlying vendor call actually does, that's treated as a bug.

## Auth model

Credentials never reach the AI client or the model — they are injected server-side by the wrapper before forwarding a call.

Current scope uses a **single service-account bot token**: one Slack bot token, held in an environment variable, used for every call regardless of which client is connected. This is simple and adequate for a single-workspace demo, but does not attribute actions to individual end users.

A production version would move to **per-user delegated OAuth with token vaulting** — each real user authorizes the wrapper via Slack's OAuth flow, tokens are stored encrypted, and the correct token is selected per session. Not implemented here; noted as the production-path difference.

## Setup

### Prerequisites

- Python 3.11+
- A Slack workspace you control, with a bot token (`xoxb-...`) and a few basic scopes (`channels:history`, `channels:read`, `users:read`)
- An Anthropic API key (for `slack_thread_digest`)
- [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server) available to run locally

### 1. Run the vendor Slack MCP server

```bash
git clone https://github.com/korotovsky/slack-mcp-server.git
cd slack-mcp-server
export SLACK_MCP_XOXB_TOKEN=xoxb-your-bot-token
go run mcp/mcp-server.go --transport sse --port 8090
```

### 2. Configure the wrapper

```bash
git clone https://github.com/<your-username>/slack-mcp-wrapper.git
cd slack-mcp-wrapper
cp .env.example .env
```

`.env`:
```
VENDOR_SLACK_MCP_URL=http://localhost:8090
ANTHROPIC_API_KEY=your-key-here
WRAPPER_PORT=8080
```

### 3. Run the wrapper

```bash
pip install -r requirements.txt
python server.py
```

### 4. Point an AI client at the wrapper

```json
{
  "mcpServers": {
    "slack-wrapper": {
      "url": "http://localhost:8080/mcp",
      "transport": "sse"
    }
  }
}
```

## Testing

- **Manual inspection:** run the wrapper, then connect with [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to call `tools/list` and confirm both vendor and composite tools appear, then exercise `tools/call` on each.
- **Contract check against the vendor:** before relying on a demo, re-run `tools/list` against the vendor server directly and diff the tool names/schemas your dispatcher depends on — vendor servers can rename or change tools without notice.

## Project structure

```
slack-mcp-wrapper/
├── server.py              # wrapper MCP server entrypoint
├── registry.py            # merges vendor + local tool lists for tools/list
├── dispatch.py            # routes tools/call to vendor or local handler
├── tools/
│   ├── health_report.py   # slack_channel_health_report
│   └── thread_digest.py   # slack_thread_digest
├── vendor_client.py       # MCP client connection to the vendor server
├── .env.example
└── requirements.txt
```

## Design decisions and known tradeoffs

- **Wrapper, not gateway.** This project intentionally does not do multi-backend routing or load balancing across replicas — that's a separate, larger project (see Roadmap). This scope is one vendor, one wrapper.
- **Single point of failure on the vendor.** If the vendor Slack MCP server is down, every tool in this wrapper is unavailable, including the composite ones. No fallback is implemented.
- **Rate limits compound.** `slack_channel_health_report` makes two vendor calls per invocation; heavy use will hit Slack's Web API rate limits faster than a single passthrough call would.
- **Vendor drift risk.** The vendor server is a dependency outside this project's control. `vendor_client.py` is kept as a single, isolated integration point specifically so a vendor API/schema change — or swapping to Slack's official `mcp.slack.com` server — only requires changes in one file.

## Roadmap

- [ ] `slack_triage_and_notify` — cross-system composite tool (Slack → Notion)
- [ ] Per-user OAuth token vaulting instead of a single service-account token
- [ ] Extend from a single-vendor wrapper to a multi-vendor gateway (aggregation + namespacing + optional load balancing across replicas of one backend)
- [ ] Swap the vendor connection to Slack's official remote MCP server (`mcp.slack.com`) as an alternative backend

## Related projects and prior art

- [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server) — the vendor server wrapped by this project
- [Slack's official MCP server](https://docs.slack.dev/ai/slack-mcp-server/) — the production alternative to the open-source vendor server used here
- [`mcp-proxy.dev`](https://mcp-proxy.dev/) — MCP Proxy Wrapper; documents the Decorator-pattern architecture this project follows
- [`metatool-ai/metamcp`](https://github.com/metatool-ai/metamcp) — reference for the multi-backend gateway pattern this project may grow into
- [FastMCP](https://gofastmcp.com/) — `Provider`/`Tool.from_tool()` primitives used for proxying and tool transformation

## License

MIT