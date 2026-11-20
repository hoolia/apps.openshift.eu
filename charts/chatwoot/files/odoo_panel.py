"""Odoo side panel of Chatwoot: what Odoo knows about the customer of a conversation.

A Chatwoot Dashboard App (odoo_panel.html) next to every conversation. It runs in the
oauth2-proxy pod on 127.0.0.1, so a request reaches it only after the staff login, with
X-Forwarded-Email set by oauth2-proxy. For a conversation it finds the customer, read only:

- the Odoo record whose x_chatwoot_url names the conversation (set by the Odoo bridge), and
  its partner; without one, the partner of the conversation contact's address, then of its
  phone number, when the bridge's Chatwoot user can read the conversation;
- that partner's sale orders and subscriptions, open and closed tickets, leads and
  opportunities, each with its Odoo address;
- the issue keys staff put in x_jira_key on those tickets and leads, with their status
  when ISSUES_URL names a Jira site.

Odoo is read with the bridge user's API key over the JSON-2 API; a part the key may not
read is reported as such and the others still show. Nothing is written anywhere. Standard
library only; prints no credential and no customer content.
"""
import base64
import concurrent.futures
import http.server
import json
import logging
import os
import re
import sys
import threading
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import odoo_bridge  # noqa: E402  (same directory: the Odoo client and HTTP helpers)

LOG = logging.getLogger("odoo-panel")
MAX_ROWS = 20
MAX_ISSUES = 10
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
LINKED = ("helpdesk.ticket", "crm.lead")


class Config:
    def __init__(self, env=os.environ):
        self.odoo_url = env["ODOO_URL"].rstrip("/")
        self.odoo_database = env.get("ODOO_DATABASE", "")
        self.odoo_key_file = env["ODOO_API_KEY_FILE"]
        self.chatwoot_url = env.get("CHATWOOT_URL", "http://chatwoot-web:3000").rstrip("/")
        self.account_id = int(env.get("CHATWOOT_ACCOUNT_ID", "1"))
        self.token_file = env.get("CHATWOOT_TOKEN_FILE", "")
        # the public address of Chatwoot as the bridge writes it into x_chatwoot_url
        self.public_url = env.get("PUBLIC_URL", "").rstrip("/")
        self.public_url_file = env.get("PUBLIC_URL_FILE", "")
        self.issues_url = env.get("ISSUES_URL", "").rstrip("/")
        self.issues_user = env.get("ISSUES_USER", "")
        self.issues_token_file = env.get("ISSUES_TOKEN_FILE", "")
        self.summary = env.get("SUMMARY", "") == "1"
        self.host = env.get("PANEL_HOST", "127.0.0.1")
        self.port = int(env.get("PANEL_PORT", "8083"))
        self.page = env.get("PANEL_PAGE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "odoo_panel.html"))
        if not self.odoo_url.startswith("https://") and not env.get("ODOO_ALLOW_HTTP"):
            raise ValueError("ODOO_URL must be an https address")
        if self.issues_url and not self.issues_url.startswith("https://") and not env.get("ISSUES_ALLOW_HTTP"):
            raise ValueError("ISSUES_URL must be an https address")

    def chatwoot_public_url(self):
        if self.public_url:
            return self.public_url
        if self.public_url_file:
            try:
                return odoo_bridge.read_file(self.public_url_file).rstrip("/")
            except OSError:
                return ""
        return ""


# digits a contact's phone number needs before it is looked up
PHONE_DIGITS = 6

def m2o(value):
    """[id, name] of a many2one as JSON-2 returns it, or (None, "")."""
    if isinstance(value, list) and len(value) == 2:
        return value[0], value[1]
    return None, ""


class Panel:
    def __init__(self, cfg, odoo=None):
        self.cfg = cfg
        self.odoo = odoo or odoo_bridge.Odoo(cfg)
        self._stage_fold = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ the customer

    def conversation_url(self, cid):
        base = self.cfg.chatwoot_public_url()
        return f"{base}/app/accounts/{self.cfg.account_id}/conversations/{cid}" if base else ""

    def linked_record(self, cid):
        """(model, record) of the ticket or lead the bridge linked to this conversation."""
        url = self.conversation_url(cid)
        if not url:
            return None
        for model in LINKED:
            rows = self.odoo.search_read(model, [["x_chatwoot_url", "=", url]], ["id", "name", "partner_id"])
            if rows:
                return model, rows[0]
        return None

    def contact(self, cid):
        """(address, phone number) of the conversation contact, read as the bridge's Chatwoot
        user; (None, None) if unreadable. The number keeps a leading + and its digits only."""
        if not self.cfg.token_file:
            return None, None
        try:
            token = odoo_bridge.read_file(self.cfg.token_file)
            conv = odoo_bridge.http_json(
                "GET", f"{self.cfg.chatwoot_url}/api/v1/accounts/{self.cfg.account_id}/conversations/{cid}",
                headers={"api_access_token": token})
        except (odoo_bridge.HttpError, OSError, ValueError):
            return None, None
        sender = ((conv or {}).get("meta") or {}).get("sender") or {}
        raw = str(sender.get("phone_number") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        # a few digits match too many partners
        phone = ("+" if raw.startswith("+") else "") + digits if len(digits) >= PHONE_DIGITS else None
        return odoo_bridge.bare_email(sender.get("email")), phone

    def partner(self, cid):
        """(partner id, partner name, how it was found, linked record) for a conversation."""
        linked = self.linked_record(cid)
        if linked:
            pid, name = m2o(linked[1].get("partner_id"))
            if pid:
                # the company, so orders and tickets of its other contacts show too
                rows = self.odoo.search_read("res.partner", [["id", "=", pid]], ["commercial_partner_id"])
                cpid, cname = m2o(rows[0].get("commercial_partner_id")) if rows else (None, "")
                return cpid or pid, cname or name, "linked", linked
        email, phone = self.contact(cid)
        # Odoo's phone_mobile_search compares the number without its formatting
        for found, domain in (("email", email and [["email", "=ilike", odoo_bridge.like_escape(email)]]),
                              ("phone", phone and [["phone_mobile_search", "=", phone]])):
            if not domain:
                continue
            rows = self.odoo.search_read("res.partner", domain, ["id", "name", "commercial_partner_id"], limit=1)
            if rows:
                cpid, cname = m2o(rows[0].get("commercial_partner_id"))
                return cpid or rows[0]["id"], cname or rows[0]["name"], found, linked
        return None, "", "none", linked

    # ------------------------------------------------------------ the parts

    def _part(self, func, *args):
        try:
            return {"rows": func(*args)}
        except odoo_bridge.HttpError as e:
            # 403 is an access right the key lacks; the panel says so instead of failing
            LOG.warning("panel part %s: %s", func.__name__, e)
            return {"error": "no access" if e.status in (401, 403) else "Odoo did not answer"}
        except (odoo_bridge.Gone, OSError, ValueError) as e:
            LOG.warning("panel part %s: %s", func.__name__, type(e).__name__)
            return {"error": "Odoo did not answer"}

    def orders(self, pid):
        rows = self.odoo.call("sale.order", "search_read",
                              domain=[["partner_id", "child_of", pid]],
                              fields=["name", "state", "amount_total", "currency_id", "date_order"],
                              order="date_order desc", limit=MAX_ROWS)
        return [{"name": r["name"], "state": r.get("state"), "amount": r.get("amount_total"),
                 "currency": m2o(r.get("currency_id"))[1], "date": (r.get("date_order") or "")[:10],
                 "url": self.odoo.url("sale.order", r["id"])} for r in rows or []]

    def subscriptions(self, pid):
        # sale_subscription marks an order as a subscription; without the module this part is empty
        rows = self.odoo.call("sale.order", "search_read",
                              domain=[["partner_id", "child_of", pid], ["is_subscription", "=", True]],
                              fields=["name", "subscription_state", "recurring_monthly", "currency_id",
                                      "next_invoice_date"],
                              order="id desc", limit=MAX_ROWS)
        return [{"name": r["name"], "state": r.get("subscription_state"), "monthly": r.get("recurring_monthly"),
                 "currency": m2o(r.get("currency_id"))[1], "next_invoice": r.get("next_invoice_date") or "",
                 "url": self.odoo.url("sale.order", r["id"])} for r in rows or []]

    def _folded(self, stage_ids):
        missing = [s for s in stage_ids if s not in self._stage_fold]
        if missing:
            rows = self.odoo.call("helpdesk.stage", "read", ids=missing, fields=["fold"])
            with self._lock:
                for r in rows or []:
                    self._stage_fold[r["id"]] = bool(r.get("fold"))
        return {s: self._stage_fold.get(s, False) for s in stage_ids}

    def tickets(self, pid):
        rows = self.odoo.call("helpdesk.ticket", "search_read",
                              domain=[["partner_id", "child_of", pid]],
                              fields=["name", "stage_id", "create_date", "x_jira_key"],
                              order="create_date desc", limit=MAX_ROWS)
        rows = rows or []
        folded = self._folded(sorted({m2o(r.get("stage_id"))[0] for r in rows if m2o(r.get("stage_id"))[0]}))
        return [{"name": r["name"], "stage": m2o(r.get("stage_id"))[1],
                 "open": not folded.get(m2o(r.get("stage_id"))[0], False),
                 "date": (r.get("create_date") or "")[:10], "issues": issue_keys(r.get("x_jira_key")),
                 "url": self.odoo.url("helpdesk.ticket", r["id"])} for r in rows]

    def opportunities(self, pid):
        rows = self.odoo.call("crm.lead", "search_read",
                              domain=[["partner_id", "child_of", pid]],
                              fields=["name", "type", "stage_id", "expected_revenue", "probability", "x_jira_key"],
                              order="create_date desc", limit=MAX_ROWS)
        return [{"name": r["name"], "type": r.get("type"), "stage": m2o(r.get("stage_id"))[1],
                 "revenue": r.get("expected_revenue"), "probability": r.get("probability"),
                 "issues": issue_keys(r.get("x_jira_key")),
                 "url": self.odoo.url("crm.lead", r["id"])} for r in rows or []]

    # ------------------------------------------------------------ issues

    def issue(self, key):
        """{key, url, status, summary} of one issue; status empty when it could not be read."""
        item = {"key": key, "url": f"{self.cfg.issues_url}/browse/{key}" if self.cfg.issues_url else "",
                "status": "", "summary": ""}
        if not (self.cfg.issues_url and self.cfg.issues_token_file):
            return item
        try:
            token = odoo_bridge.read_file(self.cfg.issues_token_file)
        except OSError:
            return item
        if self.cfg.issues_user:
            auth = "Basic " + base64.b64encode(f"{self.cfg.issues_user}:{token}".encode()).decode()
        else:
            auth = "Bearer " + token
        try:
            data = odoo_bridge.http_json(
                "GET", f"{self.cfg.issues_url}/rest/api/2/issue/{urllib.parse.quote(key)}?fields=status,summary",
                headers={"Authorization": auth}, timeout=5)
        except (odoo_bridge.HttpError, OSError, ValueError) as e:
            LOG.info("issue %s: %s", key, e if isinstance(e, odoo_bridge.HttpError) else type(e).__name__)
            return item
        fields = (data or {}).get("fields") or {}
        item["status"] = ((fields.get("status") or {}).get("name") or "")[:60]
        item["summary"] = (fields.get("summary") or "")[:200]
        return item

    def issues(self, keys):
        keys = list(dict.fromkeys(keys))[:MAX_ISSUES]
        if not keys:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(self.issue, keys))

    # ------------------------------------------------------------ the answer

    def context(self, cid):
        pid, name, found, linked = self.partner(cid)
        out = {"conversation": cid, "found": found, "summary": self.cfg.summary,
               "record": None, "partner": None}
        if linked:
            model, row = linked
            out["record"] = {"model": model, "name": row.get("name"), "url": self.odoo.url(model, row["id"])}
        if not pid:
            return out
        out["partner"] = {"name": name, "url": self.odoo.url("res.partner", pid)}
        out["orders"] = self._part(self.orders, pid)
        out["subscriptions"] = self._part(self.subscriptions, pid)
        out["tickets"] = self._part(self.tickets, pid)
        out["opportunities"] = self._part(self.opportunities, pid)
        keys = []
        for part in ("tickets", "opportunities"):
            for row in out[part].get("rows") or []:
                keys += row["issues"]
        out["issues"] = {"rows": self.issues(keys)}
        return out


def issue_keys(value):
    return ISSUE_KEY.findall(value or "") if isinstance(value, str) else []


def make_handler(panel, page):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "odoo-panel"
        sys_version = ""

        def log_message(self, *args):
            pass

        def send(self, status, body=b"", content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/healthz":
                return self.send(200)
            if parts.path in ("/odoo-panel", "/odoo-panel/"):
                return self.send(200, page, "text/html; charset=utf-8")
            if parts.path != "/odoo-panel/api/context":
                return self.send(404)
            # oauth2-proxy sets it after the staff login; only the gate reaches this port
            email = self.headers.get("X-Forwarded-Email", "")
            if not email:
                return self.send(401)
            cid = (urllib.parse.parse_qs(parts.query).get("conversation") or [""])[0]
            if not cid.isascii() or not cid.isdigit() or len(cid) > 12:
                return self.send(400, json.dumps({"error": "conversation is a number"}).encode())
            try:
                out = panel.context(int(cid))
            except (odoo_bridge.HttpError, odoo_bridge.Gone, OSError, ValueError) as e:
                LOG.warning("panel %s: conversation %s: %s", email, cid,
                            e if isinstance(e, odoo_bridge.HttpError) else type(e).__name__)
                return self.send(502, json.dumps({"error": "Odoo did not answer"}).encode())
            LOG.info("panel %s: conversation %s (%s)", email, cid, out["found"])
            self.send(200, json.dumps(out).encode())

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config()
    with open(cfg.page, "rb") as f:
        page = f.read()
    server = http.server.ThreadingHTTPServer((cfg.host, cfg.port), make_handler(Panel(cfg), page))
    server.daemon_threads = True
    LOG.info("odoo panel listening on %s:%d, issues %s", cfg.host, cfg.port, "on" if cfg.issues_url else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()
