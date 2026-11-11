#!/usr/bin/env python3
"""Login bridge: the identity oauth2-proxy checked becomes a Chatwoot session.

Runs next to oauth2-proxy in one pod and listens on 127.0.0.1 only, so nothing but that
oauth2-proxy can send it the X-Forwarded-Email header it trusts. oauth2-proxy sends it
GET /app/login. Without sso_auth_token in the query the bridge finds or creates the
Chatwoot user of that email through the Platform API, adds it to the account and
redirects the browser to the user's one-time login link. With sso_auth_token the request
is the browser following that link, and the bridge passes it to Chatwoot unchanged.

Standard library only. Configuration from the environment:
  CHATWOOT_URL         internal address of Chatwoot web, e.g. http://chatwoot-web:3000
  FRONTEND_URL         public https address; or FRONTEND_URL_FILE holding it
  ACCOUNT_ID           Chatwoot account the users join
  ALLOWED_GROUPS       comma separated; a user needs one of them; empty: any user
  ADMIN_GROUPS         comma separated; members become administrator, others agent;
                       empty: new users are agents and existing roles stay
  RESERVED_EMAILS      comma separated; never logged in (the break-glass super admin)
  PLATFORM_TOKEN_FILE  file with the Platform App access token

Never a login for an address under the reserved domain .invalid or in RESERVED_EMAILS,
and never for a super admin: Chatwoot refuses those (chart_initializer.rb) and the bridge
answers 403.
  LISTEN_PORT          default 8081
"""
import http.client
import http.server
import json
import logging
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("login-bridge")
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                   r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
TOKEN = re.compile(r"^[0-9a-f]{64}$")
LOOPBACK = {"127.0.0.1", "::1"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "content-length"}
TIMEOUT = 10


class Denied(Exception):
    """The request may not log in; the reason is logged, never shown."""


class Upstream(Exception):
    """Chatwoot did not answer as expected; the detail is logged, never shown."""


class Refused(Exception):
    """Chatwoot refused the user (HTTP 403: a super admin); the login is denied."""


def _split(value):
    return {g.strip().lstrip("/") for g in (value or "").split(",") if g.strip().lstrip("/")}


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.chatwoot_url = env["CHATWOOT_URL"].rstrip("/")
        frontend = env.get("FRONTEND_URL", "")
        if not frontend and env.get("FRONTEND_URL_FILE"):
            with open(env["FRONTEND_URL_FILE"], encoding="utf-8") as handle:
                frontend = handle.read().strip()
        if not frontend.startswith("https://"):
            raise ValueError("FRONTEND_URL must be an https address")
        self.frontend_url = frontend.rstrip("/")
        self.account_id = int(env["ACCOUNT_ID"])
        self.allowed_groups = _split(env.get("ALLOWED_GROUPS"))
        self.admin_groups = _split(env.get("ADMIN_GROUPS"))
        self.reserved = {e.strip().lower() for e in env.get("RESERVED_EMAILS", "").split(",") if e.strip()}
        self.token_file = env["PLATFORM_TOKEN_FILE"]
        self.port = int(env.get("LISTEN_PORT", "8081"))

    def token(self):
        # read on every call, so a rotated Secret needs no restart
        with open(self.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
        if not token:
            raise Upstream("platform token file is empty")
        return token


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def platform(cfg, method, path, body=None):
    """One Platform API call; returns the decoded JSON body. Raises Upstream on any error."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(cfg.chatwoot_url + path, data=data, method=method, headers={
        "api_access_token": cfg.token(), "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise Refused("%s %s: HTTP 403" % (method, path)) from None
        raise Upstream("%s %s: HTTP %s" % (method, path, e.code)) from None
    except (OSError, ValueError) as e:
        raise Upstream("%s %s: %s" % (method, path, type(e).__name__)) from None


def identity(headers, client_ip, cfg):
    """(email, name, role) of an allowed request; raises Denied otherwise.

    role is administrator or agent, or None to leave an existing user's role alone.
    """
    if client_ip not in LOOPBACK:
        raise Denied("request not from the local oauth2-proxy")
    emails = headers.get_all("X-Forwarded-Email") or []
    if len(emails) != 1:
        raise Denied("%d X-Forwarded-Email headers" % len(emails))
    email = emails[0].strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise Denied("malformed email")
    if email.endswith(".invalid") or email in cfg.reserved:
        raise Denied("reserved address")
    groups = set()
    for value in headers.get_all("X-Forwarded-Groups") or []:
        groups |= _split(value)
    if cfg.allowed_groups and not cfg.allowed_groups & groups:
        raise Denied("not in an allowed group")
    name = (headers.get("X-Forwarded-Preferred-Username") or "").strip()[:100] or email.split("@", 1)[0]
    if not cfg.admin_groups:
        return email, name, None
    return email, name, "administrator" if cfg.admin_groups & groups else "agent"


def _password():
    # never used to log in: the user has no way to learn it
    return secrets.token_urlsafe(32) + "aA1!"


def login_link(cfg, email, name, role):
    """Find or create the user, put it in the account and return its one-time login link."""
    user = platform(cfg, "POST", "/platform/api/v1/users", {"name": name, "email": email, "password": _password()})
    # the response also carries the user's own API token: keep only the id
    try:
        user_id = int(user["id"])
    except (TypeError, KeyError, ValueError):
        raise Upstream("user response without id") from None
    path = "/platform/api/v1/accounts/%d/account_users" % cfg.account_id
    if role is None:
        members = platform(cfg, "GET", path) or []
        if not any(isinstance(m, dict) and m.get("user_id") == user_id for m in members):
            platform(cfg, "POST", path, {"user_id": user_id, "role": "agent"})
    else:
        platform(cfg, "POST", path, {"user_id": user_id, "role": role})
    link = (platform(cfg, "GET", "/platform/api/v1/users/%d/login" % user_id) or {}).get("url", "")
    check_link(cfg, link, email)
    return link


def check_link(cfg, link, email):
    """The link must lead to this instance's login page, for this email, with a token."""
    prefix = cfg.frontend_url + "/app/login?"
    if not isinstance(link, str) or not link.startswith(prefix):
        raise Upstream("login link outside the instance")
    query = urllib.parse.parse_qs(link[len(prefix):], strict_parsing=True)
    if query.get("email") != [email] or not TOKEN.match((query.get("sso_auth_token") or [""])[0]):
        raise Upstream("login link malformed")
    if set(query) != {"email", "sso_auth_token"}:
        raise Upstream("login link carries extra parameters")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "login-bridge"
    sys_version = ""
    cfg = None

    def log_message(self, fmt, *args):
        # the query holds one-time tokens: log the path only
        LOG.info("%s %s %s", self.command, urllib.parse.urlsplit(self.path).path, args[1] if len(args) > 1 else "")

    def log_request(self, code="-", size="-"):
        self.log_message("%s", "", str(code))

    def _plain(self, status, text):
        body = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parts = urllib.parse.urlsplit(self.path)
        if parts.path == "/healthz" and self.client_address[0] in LOOPBACK:
            return self._plain(200, "ok")
        if parts.path.startswith("/app/login/") and ".." not in parts.path:
            # other login screens of the dashboard (its SAML page): the one login is ours
            self.send_response(302)
            self.send_header("Location", "/app/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if parts.path != "/app/login":
            return self._plain(404, "Not found")
        query = urllib.parse.parse_qs(parts.query)
        if "sso_auth_token" in query:
            return self._pass_through(parts.query)
        if "error" in query:
            return self._plain(403, "Single sign-on failed. Sign out at /oauth2/sign_out and try again.")
        try:
            email, name, role = identity(self.headers, self.client_address[0], self.cfg)
        except Denied as e:
            LOG.warning("login refused: %s", e)
            return self._plain(403, "Access denied.")
        try:
            link = login_link(self.cfg, email, name, role)
        except Refused as e:
            LOG.warning("login of %s refused by Chatwoot: %s", email, e)
            return self._plain(403, "Access denied.")
        except (Upstream, OSError) as e:
            LOG.error("login of %s failed: %s", email, e)
            return self._plain(502, "Chatwoot is not reachable. Try again in a minute.")
        LOG.info("login of %s", email)
        self.send_response(302)
        self.send_header("Location", link)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _pass_through(self, query):
        """The browser follows its login link: fetch the login page from Chatwoot."""
        target = urllib.parse.urlsplit(self.cfg.chatwoot_url)
        conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=TIMEOUT)
        headers = {"Host": urllib.parse.urlsplit(self.cfg.frontend_url).netloc,
                   "X-Forwarded-Proto": "https", "Accept": self.headers.get("Accept", "*/*")}
        try:
            conn.request("GET", "/app/login?" + query, headers=headers)
            response = conn.getresponse()
            body = response.read()
        except OSError as e:
            LOG.error("login page not reachable: %s", type(e).__name__)
            return self._plain(502, "Chatwoot is not reachable. Try again in a minute.")
        finally:
            conn.close()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self):
        self._plain(405, "Method not allowed")

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _refuse  # noqa: N815


def server(cfg):
    handler = type("BoundHandler", (Handler,), {"cfg": cfg})
    return http.server.ThreadingHTTPServer(("127.0.0.1", cfg.port), handler)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    httpd = server(Config())
    LOG.info("listening on 127.0.0.1:%d", httpd.server_address[1])
    httpd.serve_forever()


if __name__ == "__main__":
    main()
