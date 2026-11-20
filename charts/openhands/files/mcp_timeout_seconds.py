"""Read the timeout of an MCP server in seconds, the unit the Agent Canvas UI uses.

The UI and the settings API take an MCP server's timeout in seconds, at most 3600. The
SDK hands the value unchanged to fastmcp 4, whose MCPConfig reads it in milliseconds:
a timeout of 60 ends every tool call after 60 ms, and a timeout the tool calls can
live with fails the form of the UI.

This module converts at the hand-over. It wraps to_fastmcp_mcp_config in
openhands.sdk.mcp.utils, the module that builds the fastmcp config for a conversation
and for the MCP test of the UI, and multiplies every timeout of at most MAX_SECONDS by
1000. A larger value is taken as milliseconds already, as the chart wrote them before
0.25.1; 0 keeps disabling the timeout.

The agent-server loads this module with --import-modules from /opt/agent-canvas/tools.
"""
import functools
import sys

# the largest timeout the UI accepts, in seconds
MAX_SECONDS = 3600


def log(msg: str) -> None:
    print(f"mcp_timeout_seconds: {msg}", file=sys.stderr, flush=True)


def to_milliseconds(config):
    """The fastmcp config with every timeout in seconds given in milliseconds."""
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    for server in (servers or {}).values():
        if not isinstance(server, dict):
            continue
        timeout = server.get("timeout")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            continue
        if 0 < timeout <= MAX_SECONDS:
            server["timeout"] = int(round(timeout * 1000))
    return config


def _patch() -> None:
    from openhands.sdk.mcp import utils

    build = utils.to_fastmcp_mcp_config
    if getattr(build, "mcp_timeout_seconds", False):
        return

    @functools.wraps(build)
    def to_fastmcp_mcp_config(*args, **kwargs):
        return to_milliseconds(build(*args, **kwargs))

    to_fastmcp_mcp_config.mcp_timeout_seconds = True
    utils.to_fastmcp_mcp_config = to_fastmcp_mcp_config


try:
    _patch()
    log("MCP server timeouts read in seconds")
except Exception as exc:  # noqa: BLE001
    log(f"not applied, MCP server timeouts stay in milliseconds: {exc}")
