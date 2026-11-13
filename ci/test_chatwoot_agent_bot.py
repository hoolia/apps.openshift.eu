"""Tests of charts/chatwoot/files/agent_bot.py against a fake Chatwoot and a fake A2A agent.

python3 -m unittest ci/test_chatwoot_agent_bot.py
"""
import hashlib
import hmac
import http.client
import http.server
import importlib.util
import json
import logging
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

# the module lives in the chart: a __pycache__ there would be packaged with it
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("agent_bot", ROOT / "charts/chatwoot/files/agent_bot.py")
agent_bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_bot)

BOT_TOKEN = "bot-" + "b" * 44
SECRET = "webhook-" + "s" * 40
SA_TOKEN = "header.sa-token.signature"


class Fake(http.server.BaseHTTPRequestHandler):
    """Chatwoot API, A2A agent and token endpoint, as far as the bridge uses them."""

    state = None

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        s = self.state
        if self.path == "/token":
            s["token_calls"].append(raw.decode())
            return self._json(200, {"access_token": "access-" + str(len(s["token_calls"])), "expires_in": 300})
        body = json.loads(raw) if raw else None
        if self.path == "/a2a":
            s["a2a"].append((body, self.headers.get("Authorization")))
            reply = s["agent"](body)
            if isinstance(reply, int):
                return self._json(reply, {})
            return self._json(200, {"jsonrpc": "2.0", "id": body["id"], **reply})
        s["chatwoot"].append((self.path, body, self.headers.get("api_access_token")))
        self._json(200, {})


def message(text):
    return {"result": {"kind": "message", "role": "agent", "contextId": "ctx-1",
                       "parts": [{"kind": "text", "text": text}]}}


def event(content="Where is my order?", status="pending", message_type="incoming", private=False, attrs=None, mid=None):
    event.counter = getattr(event, "counter", 0) + 1
    return {"event": "message_created", "id": mid or event.counter, "content": content,
            "message_type": message_type, "private": private,
            "account": {"id": 1}, "inbox": {"id": 3}, "sender": {"name": "Ann", "email": "ann@example.com"},
            "conversation": {"id": 7, "status": status, "custom_attributes": dict(attrs or {"tier": "gold"})}}


class Base(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.state = {"a2a": [], "chatwoot": [], "token_calls": [], "agent": lambda b: message("Tomorrow.")}
        Fake.state = self.state
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        (d / "bot-token").write_text(BOT_TOKEN)
        (d / "webhook-secret").write_text(SECRET)
        (d / "token").write_text(SA_TOKEN)
        self.env = {"CHATWOOT_URL": base, "BOT_TOKEN_FILE": str(d / "bot-token"),
                    "WEBHOOK_SECRET_FILE": str(d / "webhook-secret"), "A2A_URL": base + "/a2a",
                    "A2A_TIMEOUT": "20", "INSTRUCTIONS": "You answer a customer.",
                    "HANDOFF_KEYWORDS": "human,person", "HANDOFF_MARKER": "HANDOFF",
                    "HANDOFF_MESSAGE": "A colleague takes over.", "FAILURE_MESSAGE": "Sorry."}
        self.base = base
        self.dir = d

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def bot(self, **env):
        return agent_bot.Bot(agent_bot.Config({**self.env, **env}))

    def paths(self):
        return [c[0] for c in self.state["chatwoot"]]


class SignatureTest(unittest.TestCase):
    def sign(self, body, ts):
        return "sha256=" + hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()

    def test_valid(self):
        ts = str(int(time.time()))
        self.assertTrue(agent_bot.verify(SECRET, ts, self.sign(b"{}", ts), b"{}"))

    def test_other_body_or_secret(self):
        ts = str(int(time.time()))
        self.assertFalse(agent_bot.verify(SECRET, ts, self.sign(b"{}", ts), b"{ }"))
        self.assertFalse(agent_bot.verify("other" + SECRET, ts, self.sign(b"{}", ts), b"{}"))

    def test_old_or_missing(self):
        ts = str(int(time.time()) - 3600)
        self.assertFalse(agent_bot.verify(SECRET, ts, self.sign(b"{}", ts), b"{}"))
        self.assertFalse(agent_bot.verify(SECRET, None, None, b"{}"))
        self.assertFalse(agent_bot.verify(SECRET, "x", "sha256=00", b"{}"))


class BotTest(Base):
    def test_answers_and_keeps_context(self):
        bot = self.bot()
        bot.handle(event())
        (sent, auth), = self.state["a2a"]
        self.assertEqual(sent["method"], "message/send")
        msg = sent["params"]["message"]
        self.assertNotIn("contextId", msg)
        self.assertTrue(msg["parts"][0]["text"].startswith("You answer a customer.\n\nWhere is my order?"))
        self.assertEqual(msg["metadata"]["chatwoot"]["contact"], {"name": "Ann", "email": "ann@example.com"})
        self.assertIsNone(auth)
        self.assertEqual(self.paths(), ["/api/v1/accounts/1/conversations/7/custom_attributes",
                                        "/api/v1/accounts/1/conversations/7/messages"])
        attrs = self.state["chatwoot"][0][1]["custom_attributes"]
        self.assertEqual(attrs, {"tier": "gold", "a2a_context_id": "ctx-1"})
        self.assertEqual(self.state["chatwoot"][1][1], {"content": "Tomorrow.", "message_type": "outgoing"})
        self.assertTrue(all(c[2] == BOT_TOKEN for c in self.state["chatwoot"]))
        # the next message continues the context, without the instructions
        bot.handle(event("And the invoice?", attrs={"a2a_context_id": "ctx-1"}))
        msg = self.state["a2a"][1][0]["params"]["message"]
        self.assertEqual(msg["contextId"], "ctx-1")
        self.assertEqual(msg["parts"][0]["text"], "And the invoice?")

    def test_ignores_what_is_not_its_turn(self):
        bot = self.bot()
        for e in (event(status="open"), event(message_type="outgoing"), event(private=True),
                  dict(event(), event="conversation_opened"), event(content="  ")):
            bot.handle(e)
        self.assertEqual(self.state["a2a"], [])
        self.assertEqual(self.state["chatwoot"], [])

    def test_same_message_once(self):
        bot = self.bot()
        bot.handle(event(mid=99))
        bot.handle(event(mid=99))
        self.assertEqual(len(self.state["a2a"]), 1)

    def test_keyword_hands_off_without_agent(self):
        self.bot().handle(event("Human!"))
        self.assertEqual(self.state["a2a"], [])
        self.assertEqual(self.paths(), ["/api/v1/accounts/1/conversations/7/messages",
                                        "/api/v1/accounts/1/conversations/7/toggle_status"])
        self.assertEqual(self.state["chatwoot"][1][1], {"status": "open"})

    def test_agent_marker_hands_off(self):
        self.state["agent"] = lambda b: message("HANDOFF")
        self.bot().handle(event())
        self.assertEqual(self.paths()[-2:], ["/api/v1/accounts/1/conversations/7/messages",
                                             "/api/v1/accounts/1/conversations/7/toggle_status"])
        self.assertEqual(self.state["chatwoot"][-2][1]["content"], "A colleague takes over.")

    def test_failure_hands_off(self):
        for failure in (500, {"error": {"code": -32000, "message": "boom"}},
                        {"result": {"kind": "task", "id": "t", "status": {"state": "failed"}}}):
            self.state["chatwoot"].clear()
            self.state["agent"] = lambda b, f=failure: f
            self.bot().handle(event())
            self.assertEqual(self.paths(), ["/api/v1/accounts/1/conversations/7/messages",
                                            "/api/v1/accounts/1/conversations/7/toggle_status"], failure)
            self.assertEqual(self.state["chatwoot"][0][1]["content"], "Sorry.")

    def test_polls_a_running_task(self):
        def agent(b):
            if b["method"] == "message/send":
                return {"result": {"kind": "task", "id": "t1", "contextId": "c9", "status": {"state": "working"}}}
            self.assertEqual(b["params"], {"id": "t1"})
            return {"result": {"kind": "task", "id": "t1", "contextId": "c9", "status": {"state": "completed"},
                               "artifacts": [{"parts": [{"kind": "text", "text": "Done."}]}]}}
        self.state["agent"] = agent
        agent_bot.time.sleep, sleep = (lambda s: None), agent_bot.time.sleep
        try:
            self.bot().handle(event())
        finally:
            agent_bot.time.sleep = sleep
        self.assertEqual([a[0]["method"] for a in self.state["a2a"]], ["message/send", "tasks/get"])
        self.assertEqual(self.state["chatwoot"][-1][1]["content"], "Done.")

    def test_service_account_token(self):
        self.bot(A2A_AUTH="serviceAccountToken", A2A_TOKEN_FILE=str(self.dir / "token")).handle(event())
        self.assertEqual(self.state["a2a"][0][1], "Bearer " + SA_TOKEN)

    def test_client_assertion_cached(self):
        bot = self.bot(A2A_AUTH="clientAssertion", A2A_TOKEN_FILE=str(self.dir / "token"),
                       A2A_TOKEN_URL=self.base + "/token", A2A_CLIENT_ID="chatwoot-bot")
        bot.handle(event())
        bot.handle(event())
        self.assertEqual(len(self.state["token_calls"]), 1)
        form = dict(p.split("=", 1) for p in self.state["token_calls"][0].split("&"))
        self.assertEqual(form["grant_type"], "client_credentials")
        self.assertEqual(form["client_id"], "chatwoot-bot")
        self.assertEqual(form["client_assertion"], SA_TOKEN)
        self.assertEqual([a[1] for a in self.state["a2a"]], ["Bearer access-1", "Bearer access-1"])

    def test_config_refuses_incomplete_auth(self):
        with self.assertRaises(ValueError):
            agent_bot.Config({**self.env, "A2A_AUTH": "clientAssertion", "A2A_TOKEN_FILE": "/x"})
        with self.assertRaises(ValueError):
            agent_bot.Config({**self.env, "A2A_AUTH": "password"})


class WebhookTest(Base):
    def setUp(self):
        super().setUp()
        self.bot_ = self.bot()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.hook = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent_bot.make_handler(self.bot_, self.pool))
        threading.Thread(target=self.hook.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hook.shutdown()
        self.hook.server_close()
        self.pool.shutdown(wait=True)
        super().tearDown()

    def post(self, body, secret=SECRET, ts=None):
        ts = ts or str(int(time.time()))
        sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        conn = http.client.HTTPConnection("127.0.0.1", self.hook.server_port, timeout=10)
        conn.request("POST", "/webhook", body, {"X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": sig,
                                                 "Content-Type": "application/json"})
        status = conn.getresponse().status
        conn.close()
        return status

    def test_unsigned_refused(self):
        body = json.dumps(event()).encode()
        self.assertEqual(self.post(body, secret="wrong" + SECRET), 401)
        self.assertEqual(self.post(body, ts=str(int(time.time()) - 900)), 401)
        self.pool.shutdown(wait=True)
        self.assertEqual(self.state["a2a"], [])

    def test_signed_answered(self):
        self.assertEqual(self.post(json.dumps(event()).encode()), 200)
        self.pool.shutdown(wait=True)
        self.assertEqual(self.state["chatwoot"][-1][1]["content"], "Tomorrow.")


class PanelTest(Base):
    def setUp(self):
        super().setUp()
        env = {k: v for k, v in self.env.items() if k not in ("BOT_TOKEN_FILE", "WEBHOOK_SECRET_FILE")}
        self.bot_ = agent_bot.Bot(agent_bot.Config(env))
        self.assertFalse(self.bot_.config.bot_enabled)
        self.panel = http.server.ThreadingHTTPServer(("127.0.0.1", 0), agent_bot.make_panel_handler(self.bot_, b"<html>"))
        threading.Thread(target=self.panel.serve_forever, daemon=True).start()

    def tearDown(self):
        self.panel.shutdown()
        self.panel.server_close()
        super().tearDown()

    def call(self, body, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.panel.server_port, timeout=10)
        h = {"Content-Type": "application/json", "Host": "cw.example.com", "X-Forwarded-Email": "sam@example.com"}
        h.update(headers or {})
        conn.request("POST", "/agent-panel/api", json.dumps(body), {k: v for k, v in h.items() if v is not None})
        r = conn.getresponse()
        out = (r.status, json.loads(r.read() or b"{}"), r.getheader("Content-Security-Policy"))
        conn.close()
        return out

    conversation = {"id": 7, "messages": [
        {"content": "My server is down", "message_type": 0},
        {"content": "We look into it", "message_type": 1},
        {"content": "check host 4", "message_type": 1, "private": True},
        {"content": "Conversation was assigned", "message_type": 2}]}

    def test_page(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.panel.server_port, timeout=10)
        conn.request("GET", "/agent-panel/")
        r = conn.getresponse()
        self.assertEqual((r.status, r.read()), (200, b"<html>"))
        self.assertIn("frame-ancestors 'self'", r.getheader("Content-Security-Policy"))

    def test_suggest_returns_text_and_never_posts(self):
        self.state["agent"] = lambda b: message("We restarted it.")
        status, body, _ = self.call({"action": "suggest", "conversation": self.conversation})
        self.assertEqual((status, body), (200, {"text": "We restarted it."}))
        self.assertEqual(self.state["chatwoot"], [])
        msg = self.state["a2a"][0][0]["params"]["message"]
        self.assertNotIn("contextId", msg)
        text = msg["parts"][0]["text"]
        self.assertIn("customer: My server is down\nstaff: We look into it\nprivate note: check host 4", text)
        self.assertNotIn("assigned", text)
        self.assertEqual(msg["metadata"]["chatwoot"]["staff"], "sam@example.com")

    def test_ask_needs_question(self):
        self.assertEqual(self.call({"action": "ask", "conversation": self.conversation})[0], 400)
        status, _, _ = self.call({"action": "ask", "question": "Which host?", "conversation": self.conversation})
        self.assertEqual(status, 200)
        self.assertIn("Question: Which host?", self.state["a2a"][0][0]["params"]["message"]["parts"][0]["text"])

    def test_refused_without_login_or_from_other_origin(self):
        self.assertEqual(self.call({"action": "summarize"}, {"X-Forwarded-Email": None})[0], 401)
        self.assertEqual(self.call({"action": "summarize"}, {"Origin": "https://evil.example.org"})[0], 403)
        self.assertEqual(self.call({"action": "summarize"}, {"Origin": "https://cw.example.com"})[0], 200)
        self.assertEqual(self.call({"action": "send"})[0], 400)

    def test_agent_failure(self):
        self.state["agent"] = lambda b: 500
        status, body, _ = self.call({"action": "summarize", "conversation": self.conversation})
        self.assertEqual((status, body), (502, {"error": "the agent did not answer"}))


if __name__ == "__main__":
    unittest.main()
