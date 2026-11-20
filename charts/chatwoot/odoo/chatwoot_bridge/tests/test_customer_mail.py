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
class TestCustomerMail(BridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.staff = cls.env["res.users"].create({"name": "Staff Member", "login": "staff@company.example",
                                                 "email": "staff@company.example",
                                                 "group_ids": [(6, 0, [cls.env.ref("base.group_user").id,
                                                                       cls.env.ref("helpdesk.group_helpdesk_user").id])]})

    def _thread(self, msg_id):
        """A ticket whose first customer mail has msg_id: later mails reach it by In-Reply-To."""
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id,
                                                     "partner_email": "jane@customer.example"})
        self.env["mail.message"].create({"model": "helpdesk.ticket", "res_id": ticket.id, "message_type": "email",
                                         "author_id": False, "email_from": "jane@customer.example",
                                         "message_id": msg_id, "body": "first"})
        return ticket

    @mute_logger("odoo.addons.mail.models.mail_thread")
    def test_customer_reply_sends_one_message_event(self):
        ticket = self._thread("<w2@customer.example>")
        self.configure_bridge()
        with self.posts() as calls:
            self.process(raw_mail("help@company.example", msg_id="<w3@customer.example>",
                                  in_reply_to="<w2@customer.example>"))
        self.assertEqual(events(calls), ["message.created"])
        message = self.env["mail.message"].search([("message_id", "=", "<w3@customer.example>")])
        self.assertEqual((message.model, message.res_id), ("helpdesk.ticket", ticket.id))
        sent = json.loads(calls[0].kwargs["data"])
        self.assertEqual(sent["message_id"], message.id)
        self.assertEqual(sent["idempotency_key"], f"message.created:helpdesk.ticket:{ticket.id}:{message.id}")

    @mute_logger("odoo.addons.mail.models.mail_thread")
    def test_customer_reply_on_a_lead_the_bridge_made_is_sent(self):
        # a support conversation moved to sales: the bridge made the lead without an event
        lead = self.env["crm.lead"].with_context(chatwoot_bridge_no_event=True).create(
            {"name": "From support", "email_from": "jane@customer.example"})
        self.env["mail.message"].create({"model": "crm.lead", "res_id": lead.id, "message_type": "email",
                                         "author_id": False, "email_from": "jane@customer.example",
                                         "message_id": "<l1@customer.example>", "body": "first"})
        self.configure_bridge()
        with self.posts() as calls:
            self.process(raw_mail("help@company.example", msg_id="<l2@customer.example>",
                                  in_reply_to="<l1@customer.example>"))
        self.assertEqual(events(calls), ["message.created"])
        self.assertEqual(json.loads(calls[0].kwargs["data"])["model"], "crm.lead")

    @mute_logger("odoo.addons.mail.models.mail_thread")
    def test_mail_from_staff_is_not_a_customer_mail(self):
        self._thread("<w4@customer.example>")
        self.configure_bridge()
        with self.posts() as calls:
            self.process(raw_mail("help@company.example", sender="Staff Member <staff@company.example>",
                                  msg_id="<w5@company.example>", in_reply_to="<w4@customer.example>"))
        self.assertEqual(calls, [])

    @mute_logger("odoo.addons.mail.models.mail_thread")
    def test_mail_from_own_addresses_is_not_a_customer_mail(self):
        self._thread("<w6@customer.example>")
        self.configure_bridge()
        for n, sender in enumerate(("odoo@company.example", "catchall@company.example", "bounce@company.example")):
            with self.posts() as calls:
                self.process(raw_mail("help@company.example", sender=sender, msg_id=f"<own{n}@company.example>",
                                      in_reply_to="<w6@customer.example>"))
            self.assertEqual(calls, [], sender)

    def test_staff_reply_and_notes_send_nothing(self):
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id,
                                                     "partner_email": "jane@customer.example"})
        self.configure_bridge()
        with self.posts() as calls:
            ticket.with_user(self.staff).message_post(body="Reply to customer", message_type="comment",
                                                      subtype_xmlid="mail.mt_comment")
            ticket.with_user(self.staff).message_post(body="Internal note", message_type="comment",
                                                      subtype_xmlid="mail.mt_note")
        self.assertEqual(calls, [])

    def test_message_written_by_the_bridge_never_echoes(self):
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id})
        self.configure_bridge()
        with self.posts() as calls:
            # without the Chatwoot id this exact message is a customer mail; the id alone stops it
            self.env["mail.message"].create({"model": "helpdesk.ticket", "res_id": ticket.id, "message_type": "email",
                                             "author_id": False, "email_from": "jane@customer.example",
                                             "body": "mirrored", "x_chatwoot_message_id": "cw-1"})
        self.assertEqual(calls, [])
        with self.posts() as calls:
            self.env["mail.message"].create({"model": "helpdesk.ticket", "res_id": ticket.id, "message_type": "email",
                                             "author_id": False, "email_from": "jane@customer.example", "body": "real"})
        self.assertEqual(events(calls), ["message.created"])

    def test_bridge_reply_is_posted_with_its_chatwoot_id(self):
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id})
        self.configure_bridge()
        user = self.env["res.users"].create({"name": "Bridge", "login": "bridge@example.com",
                                             "group_ids": [(6, 0, [self.env.ref("helpdesk.group_helpdesk_user").id])]})
        with self.posts() as calls:
            message = ticket.with_user(user).message_post(body="answer", message_type="comment",
                                                          subtype_xmlid="mail.mt_comment",
                                                          x_chatwoot_message_id="cw-7")
        self.assertEqual(message.x_chatwoot_message_id, "cw-7")
        self.assertEqual(calls, [])
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            ticket.with_user(user).message_post(body="again", message_type="comment", x_chatwoot_message_id="cw-7")
            self.env.flush_all()

    def test_customer_mail_filter(self):
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id})
        Message = self.env["mail.message"]

        def msg(**vals):
            # mail.message defaults the author to the current (internal) user; the gateway passes it
            base = {"model": "helpdesk.ticket", "res_id": ticket.id, "message_type": "email", "author_id": False,
                    "email_from": "jane@customer.example", "body": "x"}
            return Message.new(dict(base, **vals))

        self.assertTrue(msg()._chatwoot_is_customer_mail())
        self.assertFalse(msg(message_type="comment")._chatwoot_is_customer_mail())
        self.assertFalse(msg(message_type="email_outgoing")._chatwoot_is_customer_mail())
        self.assertFalse(msg(message_type="notification")._chatwoot_is_customer_mail())
        self.assertFalse(msg(model="res.partner", res_id=self.env.user.partner_id.id)._chatwoot_is_customer_mail())
        self.assertFalse(msg(author_id=self.staff.partner_id.id, email_from="staff@company.example")._chatwoot_is_customer_mail())
        self.assertFalse(msg(email_from="")._chatwoot_is_customer_mail())
        self.assertFalse(msg(x_chatwoot_message_id="cw-9")._chatwoot_is_customer_mail())
        self.assertFalse(msg(email_from="odoo@company.example")._chatwoot_is_customer_mail())
        customer = self.env["res.partner"].create({"name": "Jane", "email": "jane@customer.example"})
        self.assertTrue(msg(author_id=customer.id)._chatwoot_is_customer_mail(), "a customer partner is a customer")

    def test_chatwoot_message_id_is_unique(self):
        ticket = self.env["helpdesk.ticket"].create({"name": "t", "team_id": self.team.id})
        vals = {"model": "helpdesk.ticket", "res_id": ticket.id, "message_type": "comment", "body": "r",
                "x_chatwoot_message_id": "cw-42"}
        self.env["mail.message"].create(vals)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env["mail.message"].create(vals)
            self.env.flush_all()
