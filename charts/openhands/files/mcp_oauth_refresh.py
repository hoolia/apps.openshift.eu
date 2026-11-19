"""Keep the MCP OAuth logins of the settings fresh with their refresh token.

The agent-server refreshes an MCP OAuth access token only when a conversation calls
that server after the token expired, and an ACP agent (Claude Code, Codex) gets only
the stored access token, which it cannot refresh. A login nobody uses for a while
dies with its refresh token: providers expire an idle refresh token, and those that
rotate refresh tokens revoke the old one on every refresh.

A thread of the agent-server refreshes every OAuth server of the settings that has a
refresh token, enabled or not, once its access token has less than REFRESH_LEAD
seconds or a quarter of its lifetime left, and at least every MAX_AGE seconds. It
builds the OAuth client the way a conversation does and stores the new tokens through
the same settings token store, so the next conversation and every ACP agent start from
a valid token. It never starts an interactive login and never deletes a stored login:
a failed refresh is logged, and retried after RETRY seconds.

A conversation keeps its tokens in memory. Once its access token expired, it reads
the stored tokens again before it refreshes, so it uses the token this thread stored
instead of a refresh token that is already rotated.

The agent-server loads this module with --import-modules from /opt/agent-canvas/tools.
"""
import asyncio
import os
import sys
import threading
import time

INTERVAL = int(os.environ.get("MCP_OAUTH_REFRESH_INTERVAL") or 300)
REFRESH_LEAD = int(os.environ.get("MCP_OAUTH_REFRESH_LEAD") or 900)
MAX_AGE = int(os.environ.get("MCP_OAUTH_REFRESH_MAX_AGE") or 86400)
RETRY = int(os.environ.get("MCP_OAUTH_REFRESH_RETRY") or 3600)
# server URL to the time a refresh of it failed
FAILED: dict[str, float] = {}


def log(msg: str) -> None:
    print(f"mcp_oauth_refresh: {msg}", file=sys.stderr, flush=True)


def due(state, now: float) -> bool:
    """Whether a stored login needs a refresh now."""
    tokens = state.tokens
    if tokens is None or tokens.access_token is None or not getattr(tokens, "refresh_token", None):
        return False
    expires_at = state.token_expires_at
    if expires_at is None:
        return True
    lifetime = getattr(tokens, "expires_in", None) or 0
    try:
        lifetime = float(lifetime)
    except (TypeError, ValueError):
        lifetime = 0.0
    left = expires_at - now
    if left < max(REFRESH_LEAD, lifetime / 4):
        return True
    # issued lifetime seconds before it expires
    return bool(lifetime) and now - (expires_at - lifetime) >= MAX_AGE


async def refresh(name: str, server, auth) -> None:
    import httpx2
    from openhands.agent_server.mcp_oauth_store import MCPSettingsOAuthTokenStore
    from openhands.sdk.mcp.oauth import NonInteractiveMCPOAuth
    from openhands.sdk.mcp.utils import _oauth_auth_from_authentication_config

    oauth = _oauth_auth_from_authentication_config(
        auth.authentication,
        mcp_oauth_token_storage=MCPSettingsOAuthTokenStore(),
        mcp_oauth_class=NonInteractiveMCPOAuth,
    )
    if oauth is None:
        return
    oauth._bind(server.url)
    async with oauth.context.lock:
        await oauth._initialize()
        if not oauth.context.can_refresh_token():
            log(f"{name}: no refresh token or client stored, skipped")
            return
        request = await oauth._refresh_token()
        async with httpx2.AsyncClient(timeout=30) as client:
            response = await client.send(request)
        if response.status_code != 200:
            error = ""
            try:
                error = str(response.json().get("error", ""))
            except ValueError:
                pass
            FAILED[server.url] = time.time()
            hint = "; log in to it again in the UI" if error == "invalid_grant" else ""
            log(f"{name}: refresh failed, HTTP {response.status_code} {error}{hint}")
            return
        if await oauth._handle_refresh_response(response):
            FAILED.pop(server.url, None)
            log(f"{name}: refreshed, valid for {oauth.context.current_tokens.expires_in} s")
        else:
            FAILED[server.url] = time.time()
            log(f"{name}: refresh response not understood")


async def run_once() -> None:
    from openhands.agent_server.persistence.store import get_settings_store
    from openhands.sdk.mcp.config import MCPOAuthAuthCredential

    settings = get_settings_store().load()
    if settings is None:
        return
    now = time.time()
    for name, server in (settings.agent_settings.mcp_config or {}).items():
        auth = server.auth
        if not isinstance(auth, MCPOAuthAuthCredential) or not server.url or auth.state is None:
            continue
        if now - FAILED.get(server.url, 0) < RETRY:
            continue
        if not due(auth.state, now):
            continue
        try:
            await refresh(name, server, auth)
        except Exception as exc:  # noqa: BLE001
            FAILED[server.url] = time.time()
            log(f"{name}: refresh failed: {type(exc).__name__}: {exc}")


def loop() -> None:
    from openhands.agent_server.persistence import store

    # the agent-server creates the settings store after it imported its modules
    while getattr(store, "_settings_store", None) is None:
        time.sleep(5)
    log(f"refreshing MCP OAuth logins every {INTERVAL} s")
    while True:
        try:
            asyncio.run(run_once())
        except Exception as exc:  # noqa: BLE001
            log(f"{type(exc).__name__}: {exc}")
        time.sleep(INTERVAL)


def _patch_reload() -> None:
    """A conversation reads the stored tokens again once its own expired."""
    from openhands.sdk.mcp.oauth import MCPOAuth

    flow = MCPOAuth.async_auth_flow

    def async_auth_flow(self, request):
        if getattr(self, "_initialized", False) and not self.context.is_token_valid():
            self._initialized = False
        return flow(self, request)

    MCPOAuth.async_auth_flow = async_auth_flow


try:
    _patch_reload()
except Exception as exc:  # noqa: BLE001
    log(f"reload before refresh not applied: {exc}")

if INTERVAL > 0:
    threading.Thread(target=loop, name="mcp-oauth-refresh", daemon=True).start()
