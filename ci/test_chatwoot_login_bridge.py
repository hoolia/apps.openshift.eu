"""Tests of charts/chatwoot/files/login_bridge.py against a fake Chatwoot.

python3 -m unittest ci/test_chatwoot_login_bridge.py
"""
import http.client
import http.server
import importlib.util
import io
import json
import logging
import pathlib
import sys
import tempfile
import threading
import unittest

# the module lives in the chart: a __pycache__ there would be packaged with it
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("login_bridge", ROOT / "charts/chatwoot/files/login_bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

PLATFORM_TOKEN = "platform-" + "p" * 40
USER_TOKEN = "user-api-token-" + "u" * 30
SSO_TOKEN = "a" * 64


class FakeChatwoot(http.server.BaseHTTPRequestHandler):
    """Platform API and login page of Chatwoot, as far as the bridge uses them."""

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

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.state["calls"].append((self.command, self.path, body, self.headers.get("api_access_token")))
        return body

    def _authorized(self):
        if self.headers.get("api_access_token") != PLATFORM_TOKEN or self.state.get("status"):
            self._json(self.state.get("status") or 401, {"error": "Invalid access_token"})
            return False
        # what the chart initializer makes Chatwoot answer for a super admin
        if any(self.path.endswith(p) for p in self.state.get("forbid", ())):
            self._json(403, {"error": "super admin"})
            return False
        return True

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/app/login?"):
            self.state["calls"].append(("GET", self.path, None, None))
            body = b"<html>dashboard</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "cw_d_session_info=x; path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._record()
        if not self._authorized():
            return
        if self.path.endswith("/login"):
            return self._json(200, {"url": self.state["link"]})
        if self.path.endswith("/account_users"):
            return self._json(200, self.state["members"])
        self._json(404, {})

    def do_POST(self):  # noqa: N802
        body = self._record()
        if not self._authorized():
            return
        if self.path == "/platform/api/v1/users":
            return self._json(200, {"id": 7, "email": body["email"], "access_token": USER_TOKEN, "pubsub_token": "x"})
        if self.path.endswith("/account_users"):
            return self._json(200, {"user_id": body["user_id"], "role": body["role"]})
        self._json(404, {})


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token_file = pathlib.Path(self.tmp.name, "platform-token")
        self.token_file.write_text(PLATFORM_TOKEN + "\n")
        self.state = {"calls": [], "members": [], "status": None,
                      "link": "https://cw.example.com/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN}
        fake = type("Fake", (FakeChatwoot,), {"state": self.state})
        self.chatwoot = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake)
        threading.Thread(target=self.chatwoot.serve_forever, daemon=True).start()
        self.cfg = self.config()
        self.httpd = bridge.server(self.cfg)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.log = io.StringIO()
        self.handler = logging.StreamHandler(self.log)
        bridge.LOG.addHandler(self.handler)
        bridge.LOG.setLevel(logging.DEBUG)

    def tearDown(self):
        bridge.LOG.removeHandler(self.handler)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.chatwoot.shutdown()
        self.chatwoot.server_close()
        self.tmp.cleanup()

    def config(self, **extra):
        env = {"CHATWOOT_URL": "http://127.0.0.1:%d" % self.chatwoot.server_address[1],
               "FRONTEND_URL": "https://cw.example.com", "ACCOUNT_ID": "1",
               "ALLOWED_GROUPS": "acme-chatwoot", "ADMIN_GROUPS": "acme-admins",
               "RESERVED_EMAILS": "superadmin@chatwoot.invalid, root@example.com",
               "PLATFORM_TOKEN_FILE": str(self.token_file), "LISTEN_PORT": "0"}
        env.update(extra)
        return bridge.Config(env)

    def get(self, path, headers=(), method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.putrequest(method, path, skip_accept_encoding=True)
        for key, value in headers:
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read().decode()
        conn.close()
        return response.status, dict(response.getheaders()), body

    def login(self, email="ann@example.com", groups="acme-chatwoot"):
        headers = [("X-Forwarded-Email", email)]
        if groups is not None:
            headers.append(("X-Forwarded-Groups", groups))
        return self.get("/app/login", headers)

    def platform_calls(self):
        return [c for c in self.state["calls"] if c[1].startswith("/platform/")]

    # --- the identity it trusts

    def test_binds_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_request_from_another_address_is_refused(self):
        headers = http.client.parse_headers(io.BytesIO(b"X-Forwarded-Email: ann@example.com\r\n"
                                                       b"X-Forwarded-Groups: acme-chatwoot\r\n\r\n"))
        for address in ("10.128.4.17", "192.168.1.5", "::ffff:10.0.0.1"):
            with self.assertRaises(bridge.Denied):
                bridge.identity(headers, address, self.cfg)

    def test_no_email_header_is_refused(self):
        status, _, _ = self.get("/app/login", [("X-Forwarded-Groups", "acme-chatwoot")])
        self.assertEqual(status, 403)
        self.assertEqual(self.platform_calls(), [])

    def test_two_email_headers_are_refused(self):
        status, _, _ = self.get("/app/login", [("X-Forwarded-Email", "ann@example.com"),
                                               ("X-Forwarded-Email", "boss@example.com"),
                                               ("X-Forwarded-Groups", "acme-chatwoot")])
        self.assertEqual(status, 403)
        self.assertEqual(self.platform_calls(), [])

    def test_malformed_email_is_refused(self):
        for email in ("ann@example.com,boss@example.com", "ann", "<ann@example.com>", "ann@example.com\tx",
                      "a@b", "ann@exa mple.com", "x" * 65 + "@example.com"):
            self.state["calls"].clear()
            status, _, _ = self.login(email)
            self.assertEqual(status, 403, email)
            self.assertEqual(self.platform_calls(), [], email)

    def test_user_outside_allowed_groups_is_refused(self):
        for groups in ("acme-admins-x", "other", "", None, "acme-chatwoot2"):
            self.state["calls"].clear()
            status, _, _ = self.login(groups=groups)
            self.assertEqual(status, 403, groups)
            self.assertEqual(self.platform_calls(), [], groups)

    def test_reserved_addresses_are_refused_before_any_call(self):
        for email in ("superadmin@chatwoot.invalid", "SuperAdmin@Chatwoot.Invalid", "x@ops.example.invalid",
                      "root@example.com", "ROOT@example.com"):
            self.state["calls"].clear()
            status, headers, _ = self.login(email=email, groups="acme-chatwoot,acme-admins")
            self.assertEqual(status, 403, email)
            self.assertNotIn("Location", headers, email)
            self.assertEqual(self.platform_calls(), [], email)
        self.assertIn("reserved address", self.log.getvalue())

    def test_super_admin_refused_by_chatwoot_at_creation(self):
        self.state["forbid"] = ("/platform/api/v1/users",)
        status, headers, body = self.login(email="boss@example.com", groups="acme-chatwoot,acme-admins")
        self.assertEqual((status, body.strip()), (403, "Access denied."))
        self.assertNotIn("Location", headers)
        self.assertEqual([c[1] for c in self.platform_calls()], ["/platform/api/v1/users"])
        self.assertIn("refused by Chatwoot", self.log.getvalue())

    def test_super_admin_refused_by_chatwoot_at_the_link(self):
        self.state["forbid"] = ("/login",)
        status, headers, _ = self.login(email="boss@example.com")
        self.assertEqual(status, 403)
        self.assertNotIn("Location", headers)
        self.assertNotIn(SSO_TOKEN, self.log.getvalue())

    def test_group_with_leading_slash_matches(self):
        status, _, _ = self.login(groups="/acme-chatwoot")
        self.assertEqual(status, 302)

    # --- the login

    def test_allowed_user_gets_its_one_time_link(self):
        status, headers, _ = self.login(email="Ann@Example.com", groups="x, acme-chatwoot")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], self.state["link"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        calls = self.platform_calls()
        self.assertEqual([(c[0], c[1]) for c in calls], [
            ("POST", "/platform/api/v1/users"),
            ("POST", "/platform/api/v1/accounts/1/account_users"),
            ("GET", "/platform/api/v1/users/7/login")])
        self.assertTrue(all(c[3] == PLATFORM_TOKEN for c in calls))
        self.assertEqual(calls[0][2]["email"], "ann@example.com")
        self.assertEqual(calls[1][2], {"user_id": 7, "role": "agent"})
        password = calls[0][2]["password"]
        self.assertGreaterEqual(len(password), 40)
        self.assertTrue(any(c.isupper() for c in password) and any(c.islower() for c in password)
                        and any(c.isdigit() for c in password) and not password.isalnum())

    def test_admin_group_makes_administrator(self):
        self.login(groups="acme-chatwoot,acme-admins")
        self.assertEqual(self.platform_calls()[1][2], {"user_id": 7, "role": "administrator"})

    def test_without_admin_groups_existing_role_stays(self):
        self.httpd.RequestHandlerClass.cfg = self.config(ADMIN_GROUPS="")
        self.state["members"] = [{"user_id": 7, "role": "administrator"}]
        status, _, _ = self.login()
        self.assertEqual(status, 302)
        self.assertEqual([(c[0], c[1]) for c in self.platform_calls()], [
            ("POST", "/platform/api/v1/users"),
            ("GET", "/platform/api/v1/accounts/1/account_users"),
            ("GET", "/platform/api/v1/users/7/login")])

    def test_without_admin_groups_new_user_is_agent(self):
        self.httpd.RequestHandlerClass.cfg = self.config(ADMIN_GROUPS="")
        self.login()
        self.assertEqual(self.platform_calls()[2][2], {"user_id": 7, "role": "agent"})

    def test_link_to_another_host_is_not_followed(self):
        for link in ("https://evil.example.org/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN,
                     "https://cw.example.com.evil.org/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN,
                     "https://cw.example.com/app/login?email=boss%40example.com&sso_auth_token=" + SSO_TOKEN,
                     "https://cw.example.com/app/login?email=ann%40example.com&sso_auth_token=short",
                     "https://cw.example.com/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN
                     + "&impersonation=true",
                     "javascript:alert(1)", ""):
            self.state["link"] = link
            status, headers, _ = self.login()
            self.assertEqual(status, 502, link)
            self.assertNotIn("Location", headers)

    def test_platform_error_gives_502_without_detail(self):
        self.state["status"] = 401
        status, _, body = self.login()
        self.assertEqual(status, 502)
        self.assertNotIn(PLATFORM_TOKEN, body)
        self.assertNotIn("401", body)

    def test_missing_token_file_gives_502(self):
        self.token_file.unlink()
        status, _, _ = self.login()
        self.assertEqual(status, 502)

    def test_token_file_is_read_on_every_call(self):
        self.token_file.write_text("rotated-" + "r" * 40)
        status, _, _ = self.login()
        self.assertEqual(status, 502)
        self.token_file.write_text(PLATFORM_TOKEN)
        status, _, _ = self.login()
        self.assertEqual(status, 302)

    def test_nothing_secret_is_logged(self):
        self.login()
        self.get("/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN)
        self.state["status"] = 500
        self.login()
        text = self.log.getvalue()
        for secret in (PLATFORM_TOKEN, USER_TOKEN, SSO_TOKEN, "sso_auth_token", "password"):
            self.assertNotIn(secret, text)
        self.assertIn("login of ann@example.com", text)

    # --- the login link coming back

    def test_link_is_passed_to_chatwoot_without_identity(self):
        status, headers, body = self.get("/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN)
        self.assertEqual((status, body), (200, "<html>dashboard</html>"))
        self.assertEqual(headers["Set-Cookie"], "cw_d_session_info=x; path=/")
        self.assertEqual(self.platform_calls(), [])
        self.assertEqual(self.state["calls"][-1][1], "/app/login?email=ann%40example.com&sso_auth_token=" + SSO_TOKEN)

    def test_error_from_chatwoot_does_not_loop(self):
        status, _, _ = self.get("/app/login?error=access-denied",
                                [("X-Forwarded-Email", "ann@example.com"), ("X-Forwarded-Groups", "acme-chatwoot")])
        self.assertEqual(status, 403)
        self.assertEqual(self.platform_calls(), [])

    # --- everything else

    def test_other_paths_and_methods_are_refused(self):
        self.assertEqual(self.get("/platform/api/v1/users")[0], 404)
        self.assertEqual(self.get("/app/login/../../platform/api/v1/users")[0], 404)
        self.assertEqual(self.get("/app/loginx")[0], 404)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.get("/app/login", [("X-Forwarded-Email", "ann@example.com")], method)[0], 405)
        self.assertEqual(self.platform_calls(), [])

    def test_other_login_screens_lead_to_the_one_login(self):
        status, headers, _ = self.get("/app/login/sso")
        self.assertEqual((status, headers["Location"]), (302, "/app/login"))
        self.assertEqual(self.platform_calls(), [])

    def test_health(self):
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_config_refuses_plain_http_frontend(self):
        with self.assertRaises(ValueError):
            self.config(FRONTEND_URL="http://cw.example.com")

    def test_frontend_url_from_file(self):
        path = pathlib.Path(self.tmp.name, "frontend-url")
        path.write_text("https://cw.example.com\n")
        env = {"CHATWOOT_URL": "http://x", "FRONTEND_URL_FILE": str(path), "ACCOUNT_ID": "1",
               "PLATFORM_TOKEN_FILE": str(self.token_file)}
        self.assertEqual(bridge.Config(env).frontend_url, "https://cw.example.com")


if __name__ == "__main__":
    unittest.main()
