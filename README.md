# slack-mcp-wrapper

An MCP server that wraps an existing Slack MCP server and adds custom orchestration tools on top of it, without modifying or forking the underlying server.

This is **not** a gateway or aggregator. It doesn't route between multiple different backends or load-balance replicas. It wraps exactly one upstream MCP server and augments it — architecturally, this is the Decorator pattern applied to MCP: forward what you don't need to change, add new behavior where you do.

Built on [FastMCP 3.x](https://gofastmcp.com/), whose provider/transform architecture does the proxying natively — this project contains **no hand-written tool registry or call dispatcher**.

> **Status:** implemented and **live-verified end to end** against a real Slack workspace: all passthrough tools (including posting into a thread), both composite tools, and the sampling + degraded digest paths. Unit tests cover the metric math and the vendor's CSV payload format.

## Why this exists

Vendor-provided MCP servers (Slack, Jira, GitHub, etc.) expose a fixed set of tools that map roughly 1:1 to their REST API. They're useful, but two things they can't do on their own:

- **Combine their own tools into something new.** Slack's API has no "give me a channel health report" endpoint — it has separate endpoints for history, search, and channel listing. Nobody upstream can build the combination for you.
- **Use the caller's intelligence.** Slack's API can't summarize a thread, because that requires an LLM — and the best-placed LLM is the one already driving the conversation. MCP has a primitive for exactly this: **sampling**, where the server asks the connected client's own model to generate text.

This wrapper sits between an AI client and a vendor Slack MCP server, forwards the tools that don't need changing, and adds composite tools that do one or both of the above.

## Architecture

```mermaid
flowchart TB
    Client["AI client + its LLM<br/>(Claude Desktop, Cursor, etc.)"]
    Vendor["Vendor Slack MCP server<br/>(korotovsky/slack-mcp-server)"]
    SlackAPI["Slack Web API"]

    subgraph Wrapper["slack-mcp-wrapper (FastMCP 3.x)"]
        Proxy["Proxy provider<br/>sources tools from the vendor server"]
        Transforms["Transforms<br/>slack_ namespace · description overrides · allowlist"]
        Local["Local composite tools<br/>slack_channel_health_report · slack_thread_digest"]
    end

    Client -- "MCP: initialize, tools/list, tools/call" --> Wrapper
    Wrapper -- "MCP sampling: 'summarize this thread'" --> Client
    Proxy --> Transforms
    Proxy -- "MCP client session" --> Vendor
    Local -- "internal MCP calls" --> Vendor
    Vendor -- "Slack Web API calls" --> SlackAPI
```

The wrapper plays three roles at once:

- **To the AI client, it is an MCP server.** The client configures one connection — to this wrapper — and never talks to the vendor server or Slack directly.
- **To the vendor Slack MCP server, it is an MCP client.** FastMCP's proxy provider opens its own MCP session to the vendor, and the composite tools reuse the same connection settings for their internal calls.
- **To the client's LLM, it is a sampling requester.** `slack_thread_digest` sends the thread text back to the connected client via MCP `sampling/createMessage` and lets the *client's* model write the summary. The wrapper holds no LLM API key and pays for no inference.

### How FastMCP 3.x replaces the hand-rolled design

An earlier design for this project planned a custom `registry.py` (merge vendor + local tool lists) and `dispatch.py` (route `tools/call` by name). FastMCP 3.0's architecture makes both redundant:

| Concern | FastMCP 3.x primitive |
|---|---|
| Source tools from the vendor server | **Proxy provider** (`create_proxy` / provider backed by an MCP client) |
| Prefix vendor tools as `slack_*` | **`Namespace` transform** |
| Rewrite tool descriptions without touching behavior | **`ToolTransform`** |
| Hide vendor tools we don't want to forward | **Visibility controls** (`enable`/`disable` allowlist) |
| Add new composite tools | Plain `@mcp.tool` functions on the same server |
| Merge + route everything | The FastMCP component pipeline, automatically |

### Why the vendor server runs as a standalone process, not a subprocess

The wrapper connects to the vendor Slack MCP server over network transport rather than spawning it as a stdio subprocess, even though both run on localhost during development. This keeps the wrapper identical whether the upstream is the local vendor process or Slack's official remote server — swapping the upstream is a config change, not a rewrite.

## Tools

### Passthrough tools (forwarded from the vendor, behavior untouched)

Tool names below are the vendor's real tool names with the `slack_` namespace prefix applied by the wrapper. The exact set is pinned by an allowlist in `overrides.py` and verified against the live vendor at startup of each phase.

| Tool | Source | Notes |
|---|---|---|
| `slack_channels_list` | Vendor | Description tightened for clearer model use |
| `slack_conversations_history` | Vendor | Also used internally by `slack_channel_health_report` |
| `slack_conversations_replies` | Vendor | Also used internally by `slack_thread_digest` |
| `slack_conversations_add_message` | Vendor | Posting is disabled by default in the vendor server unless explicitly enabled |

Vendor tools outside the allowlist (user groups, join/leave, mark-read, user search, etc.) are hidden, not forwarded. Live testing showed the vendor's actual tool set differs from its own README (it varies by token type) — which is exactly why the allowlist pins names instead of forwarding whatever appears.

> **Vendor payload format:** korotovsky v1.3.0 returns message data as **CSV text**, not JSON (`MsgID,UserID,UserName,...,Text,...` — `MsgID` is the Slack timestamp). The composite tools parse this in `upstream.py` (`messages_from_payload`), which also accepts JSON shapes for other upstreams.

### Composite tools (new capabilities added by this wrapper)

**`slack_channel_health_report`**
Pulls a channel's recent history via the vendor's `conversations_history` tool and computes activity metrics locally: messages per participant, average response gap, most/least active posters. Pure orchestration — one vendor call plus local computation. (The vendor exposes no member-list tool, so metrics are derived from posting activity, not channel membership.)

**`slack_thread_digest`**
Pulls a thread's full text via the vendor's `conversations_replies` tool, then requests a summary **from the connected client's own LLM via MCP sampling** and returns a short digest. No server-side API key, no server-side inference cost. If the connected client doesn't support sampling, the tool degrades gracefully: it returns the collected thread text with a note so the client's model can summarize it directly.

## Tool description overrides

Tool descriptions forwarded from the vendor are not always passed through verbatim. Descriptions may be rewritten (via FastMCP's `ToolTransform`) to be more decision-relevant for a model choosing between tools — what it returns, when to prefer it over a similar tool. The rule followed throughout: **override the description, never the behavior.** If a description doesn't match what the underlying vendor call actually does, that's treated as a bug.

## Auth model

Slack credentials never reach the AI client, the model, or even this wrapper — they live in the **vendor server's** environment. The wrapper itself holds no secrets except an optional bearer key for its vendor connection:

- **Slack token** (`xoxb-...`): held by the vendor server process.
- **LLM inference**: performed by the connected client's own model via MCP sampling — the wrapper has no Anthropic/OpenAI key at all.
- **Vendor bearer key** (optional): if the vendor server is started with `SLACK_MCP_API_KEY`, the wrapper presents it on its upstream connection.

Current scope uses a **single service-account bot token** in the vendor server: adequate for a single-workspace demo, but it does not attribute actions to individual end users. The production path is per-user delegated OAuth — which is exactly the model Slack's official MCP server imposes (next section).

## The official Slack MCP server (the production upstream)

Slack ships an official remote MCP server. This wrapper is designed so that pointing at it instead of the local vendor is a configuration change in `upstream.py`/`.env`, not a rewrite. What it requires, per [Slack's docs](https://docs.slack.dev/ai/slack-mcp-server/):

- **Endpoint:** `https://mcp.slack.com/mcp` — JSON-RPC 2.0 over **streamable HTTP only** (no SSE, no dynamic client registration).
- **Auth:** confidential OAuth 2.0 with the app's `client_id`/`client_secret`; user tokens via `https://slack.com/oauth/v2_user/authorize` + `https://slack.com/api/oauth.v2.user.access`. Scopes vary per tool.
- **App requirements:** only **directory-published or internal** Slack apps may connect; workspace admins must approve the integration; IP allowlists may apply.
- **Tools:** search (messages/files/users/channels/emoji), read/send messages, threads, reactions, channel creation, member lists, files, canvases, user info.
- **Rate limits:** per-tool, roughly Tier 2 (~20/min) to Tier 4 (~100/min).

Those prerequisites (published app + admin approval + OAuth client) are why local development uses the open-source [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server), which needs only a bot token. The swap also implies renaming entries in the passthrough allowlist, since the official server's tool names differ from the vendor's — that's the one file (`overrides.py`) expected to change.

## Setup

### Prerequisites

- Python 3.11+
- A Slack workspace you control, with a bot token (`xoxb-...`) and basic scopes (`channels:history`, `channels:read`, `users:read`)
- Node.js (the vendor server ships as an npm package wrapping its Go binary — no Go toolchain needed)
- An MCP client that supports **sampling** for `slack_thread_digest` (MCP Inspector does; the other tools work regardless)

The bot token needs these **Bot Token Scopes** (the vendor lists all conversation types at boot, so the read scopes are all required): `channels:read`, `channels:history`, `groups:read`, `im:read`, `mpim:read`, `users:read`, plus `chat:write` if posting is enabled. Reinstall the app after adding scopes, and `/invite` the bot to the channels it should read.

### 1. Run the vendor Slack MCP server

```powershell
$env:SLACK_MCP_XOXB_TOKEN = "xoxb-your-bot-token"
$env:SLACK_MCP_ADD_MESSAGE_TOOL = "true"   # optional: enables posting
npx -y slack-mcp-server@latest --transport sse   # serves on 127.0.0.1:13080/sse
```

### 2. Configure the wrapper

```bash
cp .env.example .env
```

`.env`:
```
VENDOR_SLACK_MCP_URL=http://127.0.0.1:13080/sse
WRAPPER_HOST=127.0.0.1
WRAPPER_PORT=8080
# VENDOR_API_KEY=only-if-vendor-started-with-SLACK_MCP_API_KEY
```

### 3. Run the wrapper

```bash
pip install -e .
python -m slack_mcp_wrapper.server
```

### 4. Point an AI client at the wrapper

```json
{
  "mcpServers": {
    "slack-wrapper": {
      "url": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

## Testing

- **Live inspection:** run vendor + wrapper, connect with [MCP Inspector](https://github.com/modelcontextprotocol/inspector), confirm `tools/list` shows the 4 passthrough + 2 composite tools, exercise each `tools/call` against a real workspace. Inspector's sampling support exercises `slack_thread_digest` end to end.
- **Unit tests:** `pytest` covers the pure metric computation behind `slack_channel_health_report` — no network, no mocks needed.
- **Contract check against the vendor:** re-run `tools/list` against the vendor server directly and diff the tool names/schemas the allowlist depends on — vendor servers can rename or change tools without notice.

## Project structure

```
slack-mcp-wrapper/
├── pyproject.toml
├── .env.example
├── src/slack_mcp_wrapper/
│   ├── config.py            # env-driven settings (vendor URL, host/port)
│   ├── upstream.py          # single integration point: proxy provider + internal client, one config
│   ├── overrides.py         # namespace, description overrides, passthrough allowlist (data, not logic)
│   ├── server.py            # assembles the FastMCP server; entrypoint
│   └── tools/
│       ├── health_report.py # slack_channel_health_report
│       └── thread_digest.py # slack_thread_digest (MCP sampling)
└── tests/
    └── test_health_metrics.py
```

## Design decisions and known tradeoffs

- **Wrapper, not gateway.** No multi-backend routing or load balancing — that's a separate, larger project (see Roadmap). One vendor, one wrapper.
- **Single point of failure on the vendor.** If the vendor Slack MCP server is down, passthrough tools disappear from `tools/list` and composite tools fail at call time (they still list, since they're local). The wrapper itself boots and serves regardless — the vendor connection is lazy — but no fallback data path exists.
- **Rate limits compound.** Composite tools make vendor calls per invocation; heavy use hits Slack's Web API rate limits faster than single passthrough calls — and against the official server, its per-tool tiers apply.
- **Sampling requires client support.** `slack_thread_digest` depends on the client implementing MCP sampling. Clients that don't get a degraded (but still useful) response. This was chosen over a server-side LLM key deliberately: the client's model is already paid for, already in context, and the wrapper stays credential-free.
- **Vendor drift risk.** The vendor server is outside this project's control. `upstream.py` + `overrides.py` are the only files that know vendor specifics, so a vendor schema change — or the swap to `mcp.slack.com` — is contained.

## Roadmap

- [ ] `slack_triage_and_notify` — cross-system composite tool (Slack → Notion); deferred to keep scope to a single vendor
- [ ] Per-user OAuth token vaulting instead of a single service-account token
- [ ] Extend from a single-vendor wrapper to a multi-vendor gateway (aggregation + namespacing + optional load balancing)
- [ ] Swap the upstream to Slack's official remote MCP server (`mcp.slack.com`)

## Related projects and prior art

- [`korotovsky/slack-mcp-server`](https://github.com/korotovsky/slack-mcp-server) — the vendor server wrapped by this project
- [Slack's official MCP server](https://docs.slack.dev/ai/slack-mcp-server/) — the production upstream this wrapper is config-swap ready for
- [FastMCP 3.x](https://gofastmcp.com/) — providers, transforms, proxying, sampling; the framework doing the heavy lifting here
- [`metatool-ai/metamcp`](https://github.com/metatool-ai/metamcp) — reference for the multi-backend gateway pattern this project may grow into

## License

MIT
