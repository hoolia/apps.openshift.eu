"""Fakes of an MCP server and an OpenAI-compatible model, as httpx transports.

All data is synthetic.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accountant_agent import config  # noqa: E402

MCP_URL = "http://mcp.test/mcp"
MODEL_URL = "http://model.test/v1"


class FakeMcp:
    """Streamable HTTP MCP server: session id, SSE or JSON replies, two pages of tools."""

    def __init__(self, tools, results=None, sse=False):
        self.tools = tools
        self.results = results or {}
        self.sse = sse
        self.calls = []
        self.sessions = 0
        self.expire_next = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        method = msg["method"]
        if method == "initialize":
            self.sessions += 1
            return self._reply(msg, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                               headers={"mcp-session-id": f"s{self.sessions}"})
        assert request.headers.get("mcp-session-id") == f"s{self.sessions}"
        if "id" not in msg:
            return httpx.Response(202)
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(404)
        if method == "tools/list":
            half = len(self.tools) // 2 or len(self.tools)
            if (msg.get("params") or {}).get("cursor") == "p2":
                return self._reply(msg, {"tools": self.tools[half:]})
            page = {"tools": self.tools[:half]}
            if half < len(self.tools):
                page["nextCursor"] = "p2"
            return self._reply(msg, page)
        if method == "tools/call":
            p = msg["params"]
            self.calls.append((p["name"], p["arguments"]))
            return self._reply(msg, self.results.get(p["name"], {"content": [{"type": "text", "text": "ok"}]}))
        return self._reply(msg, None, error={"code": -32601, "message": "no such method"})

    def _reply(self, msg, result, headers=None, error=None):
        body = {"jsonrpc": "2.0", "id": msg["id"]}
        body.update({"error": error} if error else {"result": result})
        if self.sse:
            text = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
            text += f"event: message\ndata: {json.dumps(body)}\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream", **(headers or {})})
        return httpx.Response(200, json=body, headers=headers or {})


class FakeModel:
    """Answers chat completions from a script: each item is a message dict or a callable."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((body, request.headers.get("authorization")))
        step = self.script.pop(0)
        message = step(body) if callable(step) else step
        return httpx.Response(200, json={"choices": [{"message": message}]})


def tool_call(name, arguments, call_id="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def answer(text):
    return {"role": "assistant", "content": text}


def router(mcp=None, model=None):
    def handle(request: httpx.Request):
        url = str(request.url)
        if url.startswith(MCP_URL) and mcp:
            return mcp.handle(request)
        if url.startswith(MODEL_URL) and model:
            return model.handle(request)
        return httpx.Response(503)
    return httpx.MockTransport(handle)


TOOLS = [
    {"name": "list_purchase_invoices", "description": "List new purchase invoices",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "draft_booking", "description": "Draft a booking", "inputSchema": {
        "type": "object", "properties": {"invoiceId": {"type": "string"}}}},
    {"name": "commit_booking", "description": "Commit an approved draft", "inputSchema": {
        "type": "object", "properties": {"draftId": {"type": "string"}, "answer": {"type": "string"}}}},
]


@pytest.fixture
def raw_config(tmp_path):
    token = tmp_path / "token"
    token.write_text("sa-token-1\n")
    return {
        "agentName": "acct",
        "publicUrl": "http://acct.test:{port}/",
        "roles": {
            "bookkeeper": {"port": 8081, "description": "Daily bookkeeping", "instructions": "Book invoices.",
                           "tools": ["ledger__*"]},
            "cfo": {"port": 8082, "description": "Weekly report", "instructions": "Report."},
            "payroll": {"enabled": False, "port": 8083},
        },
        "model": {"baseUrl": MODEL_URL + "/", "name": "test-model", "tokenFile": str(token), "maxToolRounds": 4},
        "mcpServers": [{"name": "ledger", "url": MCP_URL, "tokenFile": str(token)}],
    }


@pytest.fixture
def cfg(raw_config):
    return config.parse(raw_config)
