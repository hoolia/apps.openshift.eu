import json
import logging
import os
import secrets
import tempfile
import time
from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..models import event as bridge_event, signing as bridge
from .common import SECRET, URL, BridgeCase, captured, raw_mail


def events(calls):
    return [json.loads(c.kwargs["data"])["event"] for c in calls]


@tagged("post_install", "-at_install")
class TestSignature(BridgeCase):

    def test_sign_and_verify(self):
        body = b'{"event":"ticket.created","id":1}'
        now = 1_800_000_000
        sig = bridge.sign(SECRET, now, body)
        self.assertTrue(sig.startswith("sha256=") and len(sig) == 71)
        self.assertTrue(bridge.verify(SECRET, str(now), body, sig, now=now + 10))
        self.assertTrue(bridge.verify(SECRET, str(now).encode(), body.decode(), sig.encode(), now=now))

    def test_verify_rejects_tampering_wrong_secret_and_replay(self):
        body = b'{"event":"ticket.created","id":1}'
        now = 1_800_000_000
        sig = bridge.sign(SECRET, now, body)
        self.assertFalse(bridge.verify(SECRET, now, body.replace(b"1}", b"2}"), sig, now=now))
        self.assertFalse(bridge.verify("another-secret-of-sufficient-length-000", now, body, sig, now=now))
        self.assertFalse(bridge.verify(SECRET, now + 1, body, sig, now=now), "the timestamp is signed")
        self.assertFalse(bridge.verify(SECRET, now, body, sig, now=now + bridge.TOLERANCE_SECONDS + 1))
        self.assertFalse(bridge.verify(SECRET, "not-a-number", body, sig, now=now))
        self.assertFalse(bridge.verify(SECRET, now, body, "", now=now))
        self.assertFalse(bridge.verify("", now, body, sig, now=now))

    def test_verify_returns_false_on_malformed_input(self):
        body = b"{}"
        now = 1_800_000_000
        sig = bridge.sign(SECRET, now, body)
        for signature in ("sha256=\u00e9" + sig[8:], sig[:-1] + "\u0661", b"sha256=\xff\xfe", None, 42, ["x"]):
            self.assertFalse(bridge.verify(SECRET, now, body, signature, now=now), repr(signature))
        # int() accepts these; the header does not
        for ts in ("\u0661\u0668\u0660\u0660", " %d" % now, "+%d" % now, "-1", True, None, b"\xff", "9" * 20):
            self.assertFalse(bridge.verify(SECRET, ts, body, sig, now=now), repr(ts))


@tagged("post_install", "-at_install")
class TestDelivery(BridgeCase):

    def _ticket(self, name="t"):
        return self.env["helpdesk.ticket"].create({"name": name, "team_id": self.team.id})

    def _rows(self, record):
        return self.env["chatwoot.bridge.event"].search(
            [("res_model", "=", record._name), ("res_id", "=", record.id)], order="id")

    def test_ticket_created_is_signed_and_carries_ids_only(self):
        self.configure_bridge()
        with self.posts() as calls, captured() as lines:
            ticket = self.env["helpdesk.ticket"].create({"name": "Private subject line", "team_id": self.team.id,
                                                         "partner_email": "jane@customer.example"})
        self.assertEqual(events(calls), ["ticket.created"])
        call = calls[0]
        self.assertEqual(call.args[0], URL)
        self.assertIs(call.kwargs["allow_redirects"], False)
        body = call.kwargs["data"]
        headers = call.kwargs["headers"]
        self.assertTrue(bridge.verify(SECRET, headers[bridge.TIMESTAMP_HEADER], body, headers[bridge.SIGNATURE_HEADER],
                                      now=time.time()))
        payload = json.loads(body)
        self.assertEqual(set(payload), {"event", "model", "id", "db", "idempotency_key"})
        self.assertEqual((payload["model"], payload["id"]), ("helpdesk.ticket", ticket.id))
        self.assertEqual(payload["idempotency_key"], f"ticket.created:helpdesk.ticket:{ticket.id}")
        self.assertNotIn(b"Private subject line", body)
        self.assertNotIn(b"jane@customer.example", body)
        self.assertEqual(headers[bridge.EVENT_HEADER], "ticket.created")
        row = self._rows(ticket)
        self.assertEqual((row.state, row.attempts, row.last_delivery), ("sent", 1, headers[bridge.DELIVERY_HEADER]))
        for line in lines:
            self.assertNotIn(SECRET, line)
            self.assertNotIn(headers[bridge.SIGNATURE_HEADER], line)

    def test_event_is_queued_in_the_transaction_and_delivered_by_the_cron(self):
        self.configure_bridge()
        cron = self.env.ref(bridge_event.CRON_XMLID)
        triggers = self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)])
        with patch("requests.post") as post:
            ticket = self._ticket()
            self.assertEqual(self._rows(ticket).state, "pending")
            self.assertGreater(self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]), triggers)
            # nothing is sent from the transaction or after its commit, only by the cron
            self.env.cr.postcommit.run()
            post.assert_not_called()

    def test_rolled_back_record_leaves_no_event(self):
        self.configure_bridge()
        with self.assertRaises(ZeroDivisionError), self.env.cr.savepoint():
            ticket = self._ticket()
            ticket_id = ticket.id
            self.assertTrue(self._rows(ticket))
            1 / 0  # noqa: B018
        self.assertFalse(self.env["chatwoot.bridge.event"].search([("res_id", "=", ticket_id),
                                                                     ("res_model", "=", "helpdesk.ticket")]))

    def test_portal_user_ticket_is_queued(self):
        self.configure_bridge()
        portal = self.env["res.users"].create({"name": "Portal", "login": "portal@customer.example",
                                               "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        ticket = self._ticket()
        self.env["chatwoot.bridge.event"].sudo().search([]).unlink()
        row = self.env["chatwoot.bridge.event"].with_user(portal)._enqueue("ticket.created", ticket.with_user(portal))
        self.assertTrue(row)
        with self.posts() as calls:
            pass
        self.assertEqual(events(calls), ["ticket.created"])

    def test_lead_created_is_queued(self):
        self.configure_bridge()
        with self.posts() as calls:
            self.env["crm.lead"].create({"name": "Lead", "team_id": self.sales.id})
        self.assertEqual(events(calls), ["lead.created"])

    def test_a_lead_the_bridge_creates_sends_no_created_event(self):
        # the bridge links a lead it makes from a support conversation itself
        self.configure_bridge()
        with self.posts() as calls:
            self.env["crm.lead"].with_context(chatwoot_bridge_no_event=True).create(
                {"name": "From support", "team_id": self.sales.id})
        self.assertEqual(calls, [])

    def test_a_model_switched_off_queues_nothing(self):
        self.configure_bridge()
        with patch.object(type(self.env["crm.lead"]), "_chatwoot_bridge_active", lambda self: False), \
                self.posts() as calls:
            self.env["crm.lead"].create({"name": "Lead", "team_id": self.sales.id})
        self.assertEqual(calls, [])

    def test_nothing_is_queued_without_url(self):
        self.configure_bridge(False, SECRET)
        with self.posts() as calls:
            ticket = self._ticket()
        self.assertEqual(calls, [])
        self.assertFalse(self._rows(ticket))

    def test_missing_secret_keeps_the_event_and_records_why(self):
        self.configure_bridge(URL, False)
        with self.posts() as calls:
            ticket = self._ticket()
        self.assertEqual(calls, [])
        row = self._rows(ticket)
        self.assertEqual((row.state, row.attempts, row.last_error), ("pending", 1, "secret not configured"))

    def test_unsafe_configuration_is_refused(self):
        # built at runtime so no credential URL sits in the source
        user, password = "user", secrets.token_hex(8)
        userinfo = "%s:%s" % (user, password)
        for url, secret in (("https://bridge.example.test/e?secret=x", SECRET),
                            ("https://%s@bridge.example.test/e" % userinfo, SECRET),
                            ("ftp://bridge.example.test/e", SECRET),
                            (URL, "short")):
            self.configure_bridge(url, secret)
            with captured(level=logging.WARNING) as lines, self.posts() as calls:
                self._ticket()
            self.assertEqual(calls, [], url)
            self.assertTrue(lines, url)
            for line in lines:
                self.assertNotIn(userinfo, line)
                self.assertNotIn(password, line)
                self.assertNotIn("secret=x", line)

    def test_secret_comes_from_a_mounted_file_first(self):
        with tempfile.NamedTemporaryFile("w", suffix=".secret", delete=False) as handle:
            handle.write(SECRET + "\n")
        self.addCleanup(os.unlink, handle.name)
        self.configure_bridge(URL, "ignored-because-the-file-wins-000000000")
        with patch.dict(os.environ, {bridge.ENV_SECRET_FILE: handle.name}):
            self.assertEqual(bridge.secret_from_env(), SECRET)
            with self.posts() as calls:
                self._ticket()
        headers = calls[0].kwargs["headers"]
        self.assertTrue(bridge.verify(SECRET, headers[bridge.TIMESTAMP_HEADER], calls[0].kwargs["data"],
                                      headers[bridge.SIGNATURE_HEADER]))
        with patch.dict(os.environ, {bridge.ENV_SECRET_FILE: handle.name + ".missing"}), captured() as lines:
            self.assertIsNone(bridge.secret_from_env())
        self.assertNotIn(SECRET, "".join(lines))

    def test_secret_is_never_read_from_the_database(self):
        self.configure_bridge(URL, False)
        self.icp.set_param("chatwoot_bridge.secret", SECRET)
        self.assertIsNone(bridge.secret_from_env())

    def test_failure_is_retried_with_backoff_then_given_up(self):
        self.configure_bridge()
        ticket = self._ticket()
        row = self._rows(ticket)
        err = ConnectionError(f"boom {SECRET}")
        with patch("requests.post", side_effect=err), captured(level=logging.WARNING) as lines:
            for attempt in range(1, bridge_event.MAX_ATTEMPTS + 1):
                row.next_attempt = fields.Datetime.now() - timedelta(seconds=1)
                self.deliver()
                self.assertEqual(row.attempts, attempt)
                self.assertEqual(row.last_error, "ConnectionError")
                if attempt < bridge_event.MAX_ATTEMPTS:
                    self.assertEqual(row.state, "pending")
                    self.assertGreater(row.next_attempt, fields.Datetime.now())
                    self.assertEqual(self.deliver(), 0, "not due before its backoff")
        self.assertEqual(row.state, "failed")
        self.assertEqual(len(lines), bridge_event.MAX_ATTEMPTS, lines)
        self.assertIn("gave up", lines[-1])
        self.assertTrue(lines[-1].startswith("ERROR"))
        for line in lines:
            self.assertNotIn(SECRET, line)
        with patch("requests.post", return_value=self.response()) as post:
            self.deliver()
            post.assert_not_called()
            row.action_retry()
            self.assertEqual((row.state, row.attempts), ("pending", 0))
            self.deliver()
        self.assertEqual(row.state, "sent")

    def test_redirect_and_error_status_are_failures(self):
        self.configure_bridge()
        for status in (301, 302, 307, 308, 400, 500):
            with self.posts(status=status) as calls:
                ticket = self._ticket()
            self.assertIs(calls[0].kwargs["allow_redirects"], False)
            row = self._rows(ticket)
            self.assertEqual((row.state, row.last_error), ("pending", f"HTTP {status}"), status)

    def test_unexpected_exception_does_not_stop_other_events(self):
        self.configure_bridge()
        first, second = self._ticket("a"), self._ticket("b")
        replies = iter([RuntimeError("unexpected"), self.response()])

        def post(*args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("requests.post", side_effect=post):
            self.assertEqual(self.deliver(), 2)
        self.assertEqual((self._rows(first).state, self._rows(first).last_error), ("pending", "RuntimeError"))
        self.assertEqual(self._rows(second).state, "sent")

    def test_same_event_is_queued_once(self):
        self.configure_bridge()
        ticket = self._ticket()
        with self.posts() as calls:
            ticket._chatwoot_bridge_notify()
            ticket._chatwoot_bridge_notify()
        self.assertEqual(events(calls), ["ticket.created"])
        ticket._chatwoot_bridge_notify()
        self.assertEqual(len(self._rows(ticket)), 1, "a later transaction does not repeat it either")

    def test_ticket_closed_on_folded_stage_only(self):
        self.configure_bridge()
        with self.posts():
            ticket = self._ticket()
        open_stage = self.team.stage_ids.filtered(lambda s: not s.fold)[-1:]
        solved = self.team.stage_ids.filtered("fold")[:1]
        with self.posts() as calls:
            ticket.stage_id = open_stage
        self.assertEqual(calls, [])
        with self.posts() as calls:
            ticket.stage_id = solved
        self.assertEqual(events(calls), ["ticket.closed"])

    def test_reopen_and_close_again_is_a_new_event(self):
        self.configure_bridge()
        with self.posts():
            ticket = self._ticket()
        open_stage = self.team.stage_ids.filtered(lambda s: not s.fold)[:1]
        solved = self.team.stage_ids.filtered("fold")[:1]
        keys = []
        for _round in range(2):
            with self.posts() as calls:
                ticket.stage_id = solved
            keys += [json.loads(c.kwargs["data"])["idempotency_key"] for c in calls]
            ticket.stage_id = open_stage
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])
        for key in keys:
            self.assertTrue(key.startswith(f"ticket.closed:helpdesk.ticket:{ticket.id}:"))

    def test_close_while_a_close_is_pending_is_not_queued_twice(self):
        self.configure_bridge()
        ticket = self._ticket()
        stages = self.team.stage_ids.filtered("fold")
        open_stage = self.team.stage_ids.filtered(lambda s: not s.fold)[:1]
        ticket.stage_id = stages[:1]
        ticket.stage_id = open_stage
        ticket.stage_id = stages[-1:]
        closed = self._rows(ticket).filtered(lambda r: r.event == "ticket.closed")
        self.assertEqual(len(closed), 1)

    def test_sent_events_are_purged_after_retention(self):
        self.configure_bridge()
        with self.posts():
            ticket = self._ticket()
        row = self._rows(ticket)
        row.sent_at = fields.Datetime.now() - timedelta(days=bridge_event.KEEP_SENT_DAYS + 1)
        self.deliver()
        self.assertFalse(row.exists())

    def test_bridge_sends_no_mail(self):
        self.configure_bridge()
        ticket = self._ticket()
        mails = self.env["mail.mail"].sudo().search_count([])
        messages = self.env["mail.message"].sudo().search_count([])
        with self.posts():
            ticket._chatwoot_bridge_notify()
            ticket._chatwoot_bridge_notify("ticket.closed")
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mails)
        self.assertEqual(self.env["mail.message"].sudo().search_count([]), messages)

    def test_outbox_is_admin_only(self):
        user = self.env["res.users"].create({"name": "Agent", "login": "agent@company.example",
                                             "group_ids": [(6, 0, [self.env.ref("base.group_user").id,
                                                                   self.env.ref("helpdesk.group_helpdesk_manager").id])]})
        Event = self.env["chatwoot.bridge.event"].with_user(user)
        self.assertFalse(Event.has_access("read"))
