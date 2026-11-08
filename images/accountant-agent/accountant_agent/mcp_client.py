"""MCP tools of the configured servers, over Streamable HTTP (JSON-RPC 2.0).

kagent 0.x passes no MCP binding to a BYO agent, so the agent finds its tools itself: it
lists the tools of every server in the configuration and offers them to the model as
`<server>__<tool>`. A server that does not answer is left out of that round.
"""

from __future__ import annotations

import fnmatch
import itertools
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import McpServer, bearer

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
_NAME = re.compile(r"[^A-Za-z0-9_-]")


class McpError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    server: str
    name: str
    qualified: str
    description: str
    input_schema: dict

    def openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.qualified,
                "description": self.description[:1024],
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


def qualify(server: str, tool: str) -> str:
    return _NAME.sub("_", f"{server}__{tool}")[:64]


def _sse_messages(text: str):
    data: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []


class McpClient:
    def __init__(self, server: McpServer, http: httpx.AsyncClient):
        self.server = server
        self.http = http
        self.session_id: str | None = None
        self._ids = itertools.count(1)
        self._ready = False

    def _headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        token = bearer(self.server.token_file)
        if token:
            h["Authorization"] = f"Bearer {token}"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self._ready:
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return h

    async def _send(self, method: str, params: dict | None = None, notify: bool = False):
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = next(self._ids)
        resp = await self.http.post(self.server.url, json=msg, headers=self._headers())
        if resp.status_code == 404 and self.session_id and method != "initialize":
            raise _SessionExpired()
        if resp.status_code >= 400:
            raise McpError(f"{self.server.name}: {method}: HTTP {resp.status_code}")
        if sid := resp.headers.get("mcp-session-id"):
            self.session_id = sid
        if notify:
            return None
        ctype = resp.headers.get("content-type", "")
        replies = list(_sse_messages(resp.text)) if "text/event-stream" in ctype else [resp.json()]
        for reply in replies:
            if reply.get("id") == msg["id"]:
                if "error" in reply:
                    raise McpError(f"{self.server.name}: {method}: {reply['error'].get('message')}")
                return reply.get("result") or {}
        raise McpError(f"{self.server.name}: {method}: no reply")

    async def initialize(self) -> None:
        self.session_id = None
        self._ready = False
        await self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "accountant-agent", "version": "0.1.0"},
            },
        )
        self._ready = True
        await self._send("notifications/initialized", notify=True)

    async def request(self, method: str, params: dict | None = None):
        if not self._ready:
            await self.initialize()
        try:
            return await self._send(method, params)
        except _SessionExpired:
            await self.initialize()
            return await self._send(method, params)

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        while True:
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self.request("tools/call", {"name": name, "arguments": arguments})


class _SessionExpired(Exception):
    pass


class ToolRegistry:
    """Tools of all servers, listed again after cache_seconds."""

    def __init__(self, servers, http: httpx.AsyncClient, cache_seconds: int = 300, clock=time.monotonic):
        self.clients = {s.name: McpClient(s, http) for s in servers}
        self.cache_seconds = cache_seconds
        self.clock = clock
        self._tools: dict[str, Tool] = {}
        self._at: float | None = None

    async def discover(self, force: bool = False) -> dict[str, Tool]:
        if not force and self._at is not None and self.clock() - self._at < self.cache_seconds:
            return self._tools
        found: dict[str, Tool] = {}
        for name, client in self.clients.items():
            try:
                listed = await client.list_tools()
            except (McpError, httpx.HTTPError, ValueError) as exc:
                log.warning("mcp: %s left out: %s", name, exc)
                continue
            for t in listed:
                q = qualify(name, t["name"])
                found[q] = Tool(name, t["name"], q, t.get("description") or "", t.get("inputSchema") or {})
        self._tools, self._at = found, self.clock()
        log.info("mcp: %d tools from %d servers", len(found), len(self.clients))
        return found

    async def for_role(self, patterns) -> dict[str, Tool]:
        tools = await self.discover()
        if not patterns:
            return tools
        return {q: t for q, t in tools.items() if any(fnmatch.fnmatchcase(q, p) for p in patterns)}

    async def call(self, tool: Tool, arguments: dict) -> dict:
        return await self.clients[tool.server].call_tool(tool.name, arguments)
