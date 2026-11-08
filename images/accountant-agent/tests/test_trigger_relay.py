import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client

import httpx

from accountant_agent import relay, trigger


def test_trigger_posts_a_non_blocking_task():
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "kind": "task", "id": "t-1", "status": {"state": "submitted"}}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        result = trigger.post("http://acct.test:8081/", "Run the daily bookkeeping.", c)
    assert result["id"] == "t-1"
    params = seen[0]["params"]
    assert seen[0]["method"] == "message/send"
    assert params["configuration"]["blocking"] is False
    assert params["message"]["parts"] == [{"kind": "text", "text": "Run the daily bookkeeping."}]


def test_trigger_raises_on_rpc_error():
    def handle(_):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -1, "message": "x"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        try:
            trigger.post("http://acct.test/", "x", c)
        except RuntimeError as exc:
            assert "A2A error" in str(exc)
        else:
            raise AssertionError("no error")


def test_trigger_usage():
    assert trigger.main([]) == 2


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        out = json.dumps({"path": self.path, "auth": self.headers.get("Authorization"), "body": body.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def test_relay_passes_path_body_and_token():
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    rel = ThreadingHTTPServer(("127.0.0.1", 0), relay.handler(f"http://127.0.0.1:{up.server_port}"))
    for s in (up, rel):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", rel.server_port, timeout=5)
        conn.request("POST", "/x?y=1", body=b'{"a":1}', headers={"Authorization": "Bearer jwt", "Content-Type": "application/json"})
        got = json.loads(conn.getresponse().read())
    finally:
        up.shutdown()
        rel.shutdown()
    assert got == {"path": "/x?y=1", "auth": "Bearer jwt", "body": '{"a":1}'}
