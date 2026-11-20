"""Outbox of bridge events.

An event row is written in the transaction that creates the ticket, lead or message, so it
exists exactly when that record exists. The cron "Chatwoot bridge: deliver events" sends
due rows one at a time, commits after each and retries a failure with backoff; after
MAX_ATTEMPTS the row is failed and stays visible under Settings > Technical > Automation.
Nothing is sent from a request or the mail fetch, and no after-commit callback is used.
"""
import json
import logging
import uuid
from datetime import timedelta

from odoo import api, fields, models

from . import signing

_logger = logging.getLogger(__name__)

CRON_XMLID = "chatwoot_bridge.cron_deliver_events"
# minutes to wait after the 1st, 2nd, ... failed attempt
BACKOFF_MINUTES = (1, 2, 5, 10, 30, 60, 120)
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
BATCH = 100
KEEP_SENT_DAYS = 30


class ChatwootBridgeEvent(models.Model):
    _name = "chatwoot.bridge.event"
    _description = "Chatwoot bridge event"
    _order = "id desc"
    _rec_name = "idempotency_key"

    event = fields.Char(required=True, readonly=True)
    res_model = fields.Char("Model", required=True, readonly=True)
    res_id = fields.Integer("Record", required=True, readonly=True)
    message_id = fields.Integer("Message", readonly=True)
    dedup_key = fields.Char(required=True, readonly=True, index=True)
    idempotency_key = fields.Char(required=True, readonly=True)
    state = fields.Selection([("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
                             default="pending", required=True, readonly=True, index=True)
    attempts = fields.Integer(readonly=True, default=0)
    next_attempt = fields.Datetime(readonly=True, default=fields.Datetime.now, index=True)
    last_error = fields.Char(readonly=True, help="Exception class or HTTP status of the last failed attempt")
    last_delivery = fields.Char(readonly=True, help="Delivery id header of the last attempt")
    sent_at = fields.Datetime(readonly=True)

    _idempotency_key_unique = models.Constraint("UNIQUE(idempotency_key)", "An event is queued once.")

    # ------------------------------------------------------------------ settings

    @api.model
    def _chatwoot_bridge_secret(self):
        """The HMAC key; a module that keeps an older environment variable overrides this."""
        return signing.secret_from_env()

    # ------------------------------------------------------------------ enqueue

    @api.model
    def _enqueue(self, event, record, message=None, repeatable=False):
        """Queue an event for a record; returns the new row or an empty recordset.

        A non-repeatable event (created, message) exists once per record or message. A
        repeatable one (closed) gets a key of its own per occurrence; while one is still
        pending a second is not queued, the pending one already tells the bridge.
        """
        if not signing.bridge_url(self.env):
            _logger.debug("Bridge off; event %s %s(%s) not queued", event, record._name, record.id)
            return self.browse()
        parts = [event, record._name, record.id] + ([message.id] if message else [])
        dedup = ":".join(str(p) for p in parts)
        Event = self.sudo()
        domain = [("dedup_key", "=", dedup)]
        if repeatable:
            domain.append(("state", "=", "pending"))
        if Event.search_count(domain, limit=1):
            return self.browse()
        row = Event.create({
            "event": event,
            "res_model": record._name,
            "res_id": record.id,
            "message_id": message.id if message else False,
            "dedup_key": dedup,
            "idempotency_key": dedup + (":" + uuid.uuid4().hex if repeatable else ""),
        })
        self._trigger_delivery()
        return row

    # ------------------------------------------------------------------ deliver

    def _payload(self):
        self.ensure_one()
        payload = {
            "event": self.event,
            "model": self.res_model,
            "id": self.res_id,
            "db": self.env.cr.dbname,
            "idempotency_key": self.idempotency_key,
        }
        if self.message_id:
            payload["message_id"] = self.message_id
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _deliver(self, url, secret):
        """One attempt for one row; records the outcome, never raises on the HTTP side."""
        self.ensure_one()
        shown = signing.log_url(url)
        if secret:
            delivery, error = signing.post(url, secret, self.event, self._payload())
        else:
            delivery, error = False, "secret not configured"
        attempts = self.attempts + 1
        if not error:
            self.write({"state": "sent", "attempts": attempts, "sent_at": fields.Datetime.now(),
                        "last_error": False, "last_delivery": delivery})
            _logger.info("Bridge event %s %s(%s) delivery %s to %s - succeeded",
                         self.event, self.res_model, self.res_id, delivery, shown)
            return True
        vals = {"attempts": attempts, "last_error": error, "last_delivery": delivery}
        if attempts >= MAX_ATTEMPTS:
            vals["state"] = "failed"
            _logger.error("Bridge event %s %s(%s) to %s failed after %d attempts, gave up: %s",
                          self.event, self.res_model, self.res_id, shown, attempts, error)
        else:
            vals["next_attempt"] = fields.Datetime.now() + timedelta(minutes=BACKOFF_MINUTES[attempts - 1])
            _logger.warning("Bridge event %s %s(%s) delivery %s to %s failed (attempt %d of %d): %s",
                            self.event, self.res_model, self.res_id, delivery, shown, attempts, MAX_ATTEMPTS, error)
        self.write(vals)
        return False

    def _next_due(self):
        """Lock the oldest due row, skipping rows another transaction holds."""
        self.flush_model(["state", "next_attempt"])
        self.env.cr.execute(
            "SELECT id FROM chatwoot_bridge_event WHERE state = 'pending' AND next_attempt <= %s"
            " ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
            (fields.Datetime.now(),))
        row = self.env.cr.fetchone()
        return self.browse(row[0]) if row else self.browse()

    @api.model
    def _cron_deliver(self, limit=BATCH):
        """Deliver due events; commits after each one when run by the cron."""
        self._purge_sent()
        url = signing.bridge_url(self.env)
        if not url:
            return 0
        secret = self._chatwoot_bridge_secret()
        in_cron = bool(self.env.context.get("cron_id"))
        done = 0
        while done < limit:
            row = self._next_due()
            if not row:
                break
            row._deliver(url, secret)
            done += 1
            if in_cron:
                self.env["ir.cron"]._commit_progress(1)
        return done

    @api.model
    def _purge_sent(self):
        cutoff = fields.Datetime.now() - timedelta(days=KEEP_SENT_DAYS)
        self.sudo().search([("state", "=", "sent"), ("sent_at", "<", cutoff)], limit=1000).unlink()

    def action_retry(self):
        """Send a failed or waiting event again from the first attempt."""
        self.filtered(lambda e: e.state != "sent").write(
            {"state": "pending", "attempts": 0, "next_attempt": fields.Datetime.now(), "last_error": False})
        self._trigger_delivery()
        return True

    @api.model
    def _trigger_delivery(self):
        # any user may queue an event (a portal or website ticket); the cron is admin data
        cron = self.sudo().env.ref(CRON_XMLID, raise_if_not_found=False)
        if cron and cron.active:
            cron._trigger()
