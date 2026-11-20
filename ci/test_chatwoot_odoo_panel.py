"""Tests of charts/chatwoot/files/odoo_panel.py against a fake Odoo JSON-2 API, Chatwoot and Jira.

python3 -m unittest ci/test_chatwoot_odoo_panel.py
"""
import base64
import http.client
import http.server
import importlib.util
import json
import logging
import pathlib
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / "charts/chatwoot/files"
SPEC = importlib.util.spec_from_file_location("odoo_panel", FILES / "odoo_panel.py")
odoo_panel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(odoo_panel)

ODOO_KEY = "odoo-key-" + "k" * 40
TOKEN = "platform-" + "t" * 39
STAFF = "staff@example.org"
JIRA_TOKEN = "jira-" + "j" * 30
PUBLIC = "https://support.example.org"


def match(row, domain):
    for field, op, value in domain:
        have = row.get(field)
        if isinstance(have, list) and have and isinstance(have[0], int):
            have = have[0]
        if op == "=" and have != value:
            return False
        if op == "=ilike" and (have or "").lower() != value.lower():
            return False
        if op == "child_of" and have not in Fake.children(value):
            return False
    return True


class Fake(http.server.BaseHTTPRequestHandler):
    state = None

    @classmethod
    def children(cls, pid):
        partners = cls.state["odoo"]["res.partner"]
        return {pid} | {k for k, p in partners.items() if (p.get("parent_id") or [None])[0] == pid}

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = self.state
        s["calls"].append(("GET", self.path))
        if self.path.startswith("/rest/api/2/issue/"):
            if self.headers.get("Authorization") != s["jira_auth"]:
                return self._json(401, {})
            key = self.path.split("/")[5].split("?")[0]
            issue = s["jira"].get(key)
            return self._json(200, {"fields": issue}) if issue else self._json(404, {})
        if self.path.startswith("/api/v1/accounts/1/conversations/"):
            # a staff member's own token; Chatwoot answers 404 for a conversation outside its inboxes
            who = (self.headers.get("api_access_token") or "").removeprefix("user-")
            if who not in s["users"]:
                return self._json(401, {})
            cid = int(self.path.rsplit("/", 1)[1])
            conv = s["chatwoot"].get(cid)
            if not conv or who not in conv.get("staff", ()):
                return self._json(404, {})
            return self._json(200, {k: v for k, v in conv.items() if k != "staff"})
        return self._json(404, {})

    def do_POST(self):
        s = self.state
        kw = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/platform/api/v1/users":
            s["calls"].append(("POST", self.path))
            if self.headers.get("api_access_token") != TOKEN:
                return self._json(401, {})
            email = kw.get("email")
            s["users"].add(email)
            return self._json(200, {"id": 9, "email": email, "access_token": "user-" + email})
        if self.headers.get("Authorization") != "bearer " + ODOO_KEY:
            return self._json(401, {"name": "werkzeug.exceptions.Unauthorized"})
        _, _, _, model, method = self.path.split("/")
        s["odoo_calls"].append((model, method, kw))
        if model in s["denied"]:
            return self._json(403, {"name": "odoo.exceptions.AccessError"})
        if method not in ("search_read", "read"):
            # read only: the panel never writes
            return self._json(400, {"name": "not allowed in the test: " + method})
        table = s["odoo"].get(model, {})
        if method == "read":
            return self._json(200, [{"id": i, **{f: table[i].get(f, False) for f in kw["fields"]}}
                                    for i in kw["ids"] if i in table])
        rows = [{**{f: r.get(f, False) for f in kw["fields"]}, "id": rid}
                for rid, r in sorted(table.items()) if match({**r, "id": rid}, kw["domain"])]
        return self._json(200, rows[: kw.get("limit") or None])


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = {}
        for name, value in {"key": ODOO_KEY, "token": TOKEN, "jira": JIRA_TOKEN}.items():
            path = pathlib.Path(cls.tmp.name) / name
            path.write_text(value + "\n")
            cls.paths[name] = str(path)

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def setUp(self):
        conv = f"{PUBLIC}/app/accounts/1/conversations/"
        Fake.state = self.state = {
            "calls": [], "odoo_calls": [], "denied": set(), "users": set(),
            "jira_auth": "Basic " + base64.b64encode(f"bot@example.com:{JIRA_TOKEN}".encode()).decode(),
            "jira": {"PROJ-1": {"status": {"name": "In Progress"}, "summary": "Login fails"}},
            "chatwoot": {11: {"id": 11, "staff": {STAFF}, "meta": {"sender": {"email": "ada@example.org"}}},
                         12: {"id": 12, "staff": {STAFF}, "meta": {"sender": {"email": "Bob <bob@example.org>"}}},
                         13: {"id": 13, "staff": {STAFF},
                              "meta": {"sender": {"email": None, "phone_number": "+31 6 1234-5678"}}},
                         14: {"id": 14, "staff": {STAFF},
                              "meta": {"sender": {"email": "nobody@example.org", "phone_number": "112"}}},
                         15: {"id": 15, "staff": {"other@example.org"}, "meta": {"sender": {"email": "bob@example.org"}}}},
            "odoo": {
                "res.partner": {40: {"name": "Acme", "commercial_partner_id": [40, "Acme"]},
                                42: {"name": "Ada", "email": "ada@example.org", "parent_id": [40, "Acme"],
                                     "commercial_partner_id": [40, "Acme"]},
                                43: {"name": "Eve", "email": "eve@example.org", "parent_id": [40, "Acme"],
                                     "commercial_partner_id": [40, "Acme"]},
                                50: {"name": "Bob", "email": "bob@example.org", "commercial_partner_id": [50, "Bob"]},
                                60: {"name": "Other", "commercial_partner_id": [60, "Other"]},
                                70: {"name": "Carol", "phone_mobile_search": "+31612345678",
                                     "commercial_partner_id": [70, "Carol"]}},
                "helpdesk.ticket": {7: {"name": "Cannot log in", "partner_id": [42, "Ada"], "stage_id": [1, "New"],
                                        "create_date": "2026-11-19 10:00:00", "x_jira_key": "PROJ-1",
                                        "x_chatwoot_url": conv + "11"},
                                    8: {"name": "Old question", "partner_id": [43, "Eve"], "stage_id": [3, "Solved"],
                                        "create_date": "2026-10-01 10:00:00", "x_jira_key": False},
                                    9: {"name": "Not theirs", "partner_id": [60, "Other"], "stage_id": [1, "New"]}},
                "helpdesk.stage": {1: {"fold": False}, 3: {"fold": True}},
                "crm.lead": {5: {"name": "More nodes", "partner_id": [42, "Ada"], "type": "opportunity",
                                 "stage_id": [2, "Proposition"], "expected_revenue": 1200.0, "probability": 40.0,
                                 "x_jira_key": "PROJ-1, PROJ-2"}},
                "sale.order": {30: {"name": "S00030", "partner_id": [43, "Eve"], "state": "sale", "amount_total": 99.5,
                                    "currency_id": [1, "EUR"], "date_order": "2026-11-01 09:00:00",
                                    "is_subscription": True, "subscription_state": "3_progress",
                                    "recurring_monthly": 99.5, "next_invoice_date": "2026-12-01"},
                               31: {"name": "S00031", "partner_id": [60, "Other"], "state": "draft"}},
            },
        }
        self.env = {"ODOO_URL": self.base, "ODOO_ALLOW_HTTP": "1", "ODOO_DATABASE": "odoo",
                    "ODOO_API_KEY_FILE": self.paths["key"], "PUBLIC_URL": PUBLIC, "CHATWOOT_URL": self.base,
                    "PLATFORM_TOKEN_FILE": self.paths["token"], "RESERVED_EMAILS": "admin@example.org",
                    "ISSUES_URL": self.base, "ISSUES_ALLOW_HTTP": "1", "ISSUES_USER": "bot@example.com",
                    "ISSUES_TOKEN_FILE": self.paths["jira"], "SUMMARY": "1"}

    def panel(self, **extra):
        return odoo_panel.Panel(odoo_panel.Config({**self.env, **extra}))

    def test_linked_ticket_shows_the_company_and_all_its_parts(self):
        out = self.panel().context(11, STAFF)
        self.assertEqual((out["found"], out["partner"]["name"], out["summary"]), ("linked", "Acme", True))
        self.assertEqual(out["record"]["model"], "helpdesk.ticket")
        self.assertTrue(out["record"]["url"].endswith("/odoo/helpdesk.ticket/7"))
        tickets = out["tickets"]["rows"]
        self.assertEqual([(t["name"], t["open"]) for t in tickets], [("Cannot log in", True), ("Old question", False)])
        self.assertEqual([o["name"] for o in out["orders"]["rows"]], ["S00030"])
        (sub,) = out["subscriptions"]["rows"]
        self.assertEqual((sub["monthly"], sub["next_invoice"], sub["currency"]), (99.5, "2026-12-01", "EUR"))
        (opp,) = out["opportunities"]["rows"]
        self.assertEqual((opp["stage"], opp["issues"]), ("Proposition", ["PROJ-1", "PROJ-2"]))
        # one look per key, with the tracker's status; an unknown key shows without one
        self.assertEqual([(i["key"], i["status"], i["summary"]) for i in out["issues"]["rows"]],
                         [("PROJ-1", "In Progress", "Login fails"), ("PROJ-2", "", "")])
        self.assertEqual(len([c for c in self.state["calls"] if "/issue/PROJ-1" in c[1]]), 1)

    def test_contact_address_finds_the_partner_without_a_link(self):
        out = self.panel().context(12, STAFF)
        self.assertEqual((out["found"], out["partner"]["name"], out["record"]), ("email", "Bob", None))
        self.assertEqual(out["tickets"]["rows"], [])

    def test_contact_phone_finds_the_partner_without_an_address(self):
        out = self.panel().context(13, STAFF)
        self.assertEqual((out["found"], out["partner"]["name"]), ("phone", "Carol"))
        (look,) = [kw["domain"] for m, meth, kw in self.state["odoo_calls"] if m == "res.partner"]
        self.assertEqual(look, [["phone_mobile_search", "=", "+31612345678"]])

    def test_a_short_number_is_not_looked_up(self):
        out = self.panel().context(14, STAFF)
        self.assertEqual(out["found"], "none")
        self.assertFalse([kw for m, meth, kw in self.state["odoo_calls"]
                          if m == "res.partner" and "phone_mobile_search" in json.dumps(kw)])

    def test_nothing_found_without_a_link_or_a_known_contact(self):
        self.state["chatwoot"][12]["meta"]["sender"] = {"email": "unknown@example.org"}
        out = self.panel().context(12, STAFF)
        self.assertEqual((out["found"], out["partner"]), ("none", None))
        self.assertNotIn("orders", out)

    def test_a_conversation_outside_the_staff_members_inboxes_is_refused_before_odoo(self):
        with self.assertRaises(odoo_panel.NotYours):
            self.panel().context(15, STAFF)
        with self.assertRaises(odoo_panel.NotYours):
            self.panel().context(99, STAFF)
        self.assertEqual(self.state["odoo_calls"], [])

    def test_the_staff_token_is_asked_once(self):
        panel = self.panel()
        panel.context(11, STAFF)
        panel.context(12, STAFF)
        self.assertEqual(len([c for c in self.state["calls"] if c == ("POST", "/platform/api/v1/users")]), 1)

    def test_a_part_the_key_may_not_read_says_so(self):
        self.state["denied"] = {"sale.order"}
        out = self.panel().context(11, STAFF)
        self.assertEqual(out["orders"], {"error": "no access"})
        self.assertEqual(out["subscriptions"], {"error": "no access"})
        self.assertEqual(len(out["tickets"]["rows"]), 2)

    def test_issues_without_a_tracker_show_keys_only(self):
        out = self.panel(ISSUES_URL="", ISSUES_TOKEN_FILE="").context(11, STAFF)
        self.assertEqual([(i["key"], i["url"], i["status"]) for i in out["issues"]["rows"]],
                         [("PROJ-1", "", ""), ("PROJ-2", "", "")])
        self.assertEqual([c for c in self.state["calls"] if "/rest/" in c[1]], [])

    def test_personal_token_is_a_bearer(self):
        self.state["jira_auth"] = "Bearer " + JIRA_TOKEN
        out = self.panel(ISSUES_USER="").context(11, STAFF)
        self.assertEqual(out["issues"]["rows"][0]["status"], "In Progress")

    def test_panel_only_reads(self):
        self.panel().context(11, STAFF)
        self.assertEqual({m for _, m, _ in self.state["odoo_calls"]} - {"search_read", "read"}, set())

    def test_issue_keys(self):
        self.assertEqual(odoo_panel.issue_keys("see PROJ-12 and ab-1, X-1, OPS-7"), ["PROJ-12", "OPS-7"])
        self.assertEqual(odoo_panel.issue_keys(False), [])

    def test_http_needs_the_login_header_and_a_number(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), odoo_panel.make_handler(self.panel(), b"<html>"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def get(path, headers=None):
                c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                c.request("GET", path, headers=headers or {})
                r = c.getresponse()
                return r.status, r.read(), r.getheader("Cache-Control")
            self.assertEqual(get("/odoo-panel/")[0], 200)
            self.assertEqual(get("/odoo-panel/api/context?conversation=11")[0], 401)
            self.assertEqual(get("/odoo-panel/api/context?conversation=11", {"X-Forwarded-Email": "admin@example.org"})[0], 401)
            who = {"X-Forwarded-Email": STAFF}
            status, body, _ = get("/odoo-panel/api/context?conversation=15", who)
            self.assertEqual((status, json.loads(body)), (403, {"error": "not a conversation of yours"}))
            self.assertEqual(get("/odoo-panel/api/context?conversation=1%2B1", who)[0], 400)
            status, body, cache = get("/odoo-panel/api/context?conversation=11", who)
            self.assertEqual((status, cache), (200, "no-store"))
            self.assertEqual(json.loads(body)["partner"]["name"], "Acme")
            self.assertEqual(get("/other", who)[0], 404)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_url_is_refused(self):
        with self.assertRaises(ValueError):
            odoo_panel.Config({**self.env, "ODOO_ALLOW_HTTP": ""})
        with self.assertRaises(ValueError):
            odoo_panel.Config({**self.env, "ISSUES_ALLOW_HTTP": ""})


if __name__ == "__main__":
    unittest.main()
