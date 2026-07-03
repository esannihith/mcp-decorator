"""Environment-driven settings.

All deployment-specific knobs live here so that swapping the upstream vendor
(e.g. local korotovsky/slack-mcp-server -> Slack's official mcp.slack.com)
is a .env change plus, at most, an allowlist update in overrides.py.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Upstream vendor MCP server. Must include the transport path
    # (e.g. http://127.0.0.1:13080/sse for SSE, .../mcp for streamable HTTP).
    vendor_slack_mcp_url: str = "http://127.0.0.1:13080/sse"

    # Bearer key for the upstream connection, only if the vendor server was
    # started with SLACK_MCP_API_KEY. The wrapper holds no other secrets:
    # the Slack token lives in the vendor process, and LLM inference happens
    # on the connected client via MCP sampling.
    vendor_api_key: str | None = None

    # Where this wrapper listens.
    wrapper_host: str = "127.0.0.1"
    wrapper_port: int = 8080


def load_settings() -> Settings:
    return Settings()
