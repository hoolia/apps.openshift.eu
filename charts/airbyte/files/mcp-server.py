"""MCP server for Airbyte over streamable HTTP (stateless, JSON responses, no SDK).

Tools list workspaces, connections, sources and destinations, start and cancel syncs and
read job status through the Airbyte API with the instance admin's API client.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airbyte_api import Airbyte, ApiError  # noqa: E402

PATH = os.environ.get("MCP_PATH", "/mcp")
PORT = int(os.environ.get("MCP_PORT", "8000"))
ALLOW_WRITE = os.environ.get("MCP_ALLOW_WRITE", "true") == "true"
VERSION = os.environ.get("AIRBYTE_VERSION", "")
api = Airbyte(timeout=60)


def _connection(c):
    sched = c.get("schedule") or {}
    return {"connectionId": c.get("connectionId"), "name": c.get("name"),
            "status": c.get("status"), "sourceId": c.get("sourceId"),
            "destinationId": c.get("destinationId"),
            "schedule": sched.get("cronExpression") or sched.get("scheduleType"),
            "workspaceId": c.get("workspaceId")}


def _job(j):
    keys = ("jobId", "status", "jobType", "connectionId", "startTime", "lastUpdatedAt",
            "duration", "bytesSynced", "rowsSynced")
    return {k: j.get(k) for k in keys if k in j}


def _find_connection(ref):
    for c in api.pages("/connections"):
        if ref in (c.get("connectionId"), c.get("name")):
            return c
    raise ValueError(f"no connection with id or name {ref!r}")


def list_workspaces():
    return [{"workspaceId": w["workspaceId"], "name": w.get("name")} for w in api.pages("/workspaces")]


def list_connections():
    return [_connection(c) for c in api.pages("/connections")]


def get_connection(connection):
    c = _find_connection(connection)
    out = _connection(c)
    out["streams"] = [{"name": s.get("name"), "syncMode": s.get("syncMode")}
                      for s in (c.get("configurations") or {}).get("streams") or []]
    return out


def list_sources():
    return [{"sourceId": s["sourceId"], "name": s.get("name"), "type": s.get("sourceType")}
            for s in api.pages("/sources")]


def list_destinations():
    return [{"destinationId": d["destinationId"], "name": d.get("name"),
             "type": d.get("destinationType")} for d in api.pages("/destinations")]


def list_jobs(connection, limit=10):
    c = _find_connection(connection)
    out = api.public("GET", f"/jobs?connectionId={c['connectionId']}&limit={int(limit)}"
                     "&orderBy=createdAt%7CDESC")
    return [_job(j) for j in out.get("data") or []]


def get_job(job_id):
    return _job(api.public("GET", f"/jobs/{int(job_id)}"))


def trigger_sync(connection):
    c = _find_connection(connection)
    return _job(api.public("POST", "/jobs", {"connectionId": c["connectionId"], "jobType": "sync"}))


def cancel_job(job_id):
    return _job(api.public("DELETE", f"/jobs/{int(job_id)}"))


CONN = {"type": "string", "description": "Connection id or name"}
JOB = {"type": "integer", "description": "Job id"}
TOOLS = {
    "list_workspaces": (list_workspaces, "List Airbyte workspaces.", {}, True),
    "list_connections": (list_connections, "List connections (source to destination) with status and schedule.", {}, True),
    "get_connection": (get_connection, "Show one connection with its streams.", {"connection": CONN}, True),
    "list_sources": (list_sources, "List configured sources.", {}, True),
    "list_destinations": (list_destinations, "List configured destinations.", {}, True),
    "list_jobs": (list_jobs, "List the latest sync jobs of a connection, newest first.",
                  {"connection": CONN, "limit": {"type": "integer", "description": "Number of jobs, default 10"}}, True),
    "get_job": (get_job, "Show status, rows and bytes of one job.", {"job_id": JOB}, True),
    "trigger_sync": (trigger_sync, "Start a sync of a connection now; returns the job.", {"connection": CONN}, False),
    "cancel_job": (cancel_job, "Cancel a running job.", {"job_id": JOB}, False),
}


def tool_list():
    out = []
    for name, (_, desc, props, readonly) in TOOLS.items():
        if not readonly and not ALLOW_WRITE:
            continue
        required = [k for k in props if k != "limit"]
        out.append({"name": name, "description": desc,
                    "inputSchema": {"type": "object", "properties": props, "required": required},
                    "annotations": {"readOnlyHint": readonly}})
    return out


def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "airbyte", "version": VERSION or "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        name = params.get("name")
        entry = TOOLS.get(name)
        if not entry or (not entry[3] and not ALLOW_WRITE):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            data = entry[0](**(params.get("arguments") or {}))
            result = {"content": [{"type": "text", "text": json.dumps(data, indent=1)}], "isError": False}
        except (ApiError, ValueError, TypeError, KeyError) as err:
            result = {"content": [{"type": "text", "text": str(err)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method {method} not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"status": "ok"})
        self._send(405)

    def do_DELETE(self):
        self._send(405)

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != PATH.rstrip("/"):
            return self._send(404)
        try:
            msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        except ValueError:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if isinstance(msg, list):
            out = [r for r in (handle(m) for m in msg) if r]
            return self._send(200, out) if out else self._send(202)
        out = handle(msg)
        return self._send(200, out) if out else self._send(202)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, fmt % args))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
