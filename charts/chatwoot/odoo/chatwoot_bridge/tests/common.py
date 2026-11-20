import logging
import os
import secrets
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase

from ..models import signing

BRIDGE_LOGGER = "odoo.addons.chatwoot_bridge"
# a fresh key per run; every test signs and verifies with this same value
SECRET = secrets.token_hex(32)
URL = "https://bridge.example.test/odoo/events"
DOMAIN = "company.example"


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append("%s %s" % (record.levelname, record.getMessage()))


@contextmanager
def captured(name=BRIDGE_LOGGER, level=logging.DEBUG):
    """assertLogs sees no INFO records under Odoo's test runner; a handler of our own does."""
    logger = logging.getLogger(name)
    handler = _Records()
    old = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)


def raw_mail(to, sender="Jane Customer <jane@customer.example>", subject="Help please",
             msg_id="<m1@customer.example>", in_reply_to=None, body="My app does not start."):
    headers = [
        "Return-Path: <%s>" % sender.split("<")[-1].rstrip(">"),
        "From: %s" % sender,
        "To: %s" % to,
        "Subject: %s" % subject,
        "Message-ID: %s" % msg_id,
        "Date: Wed, 04 Nov 2026 10:00:00 +0000",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
    ]
    if in_reply_to:
        headers += ["In-Reply-To: %s" % in_reply_to, "References: %s" % in_reply_to]
    return "\r\n".join(headers) + "\r\n\r\n" + body + "\r\n"


class BridgeCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.team = cls.env.ref("helpdesk.helpdesk_team1")
        cls.sales = cls.env.ref("sales_team.team_sales_department")
        cls.icp.set_param("mail.catchall.domain", DOMAIN)
        cls.icp.set_param("mail.default.from", "odoo@" + DOMAIN)
        cls.icp.set_param("mail.catchall.alias", "catchall")
        cls.icp.set_param("mail.bounce.alias", "bounce")

    def configure_bridge(self, url=URL, secret=SECRET):
        self.icp.set_param(signing.PARAM_URL, url)
        env = {k: v for k, v in os.environ.items() if k not in (signing.ENV_SECRET, signing.ENV_SECRET_FILE)}
        if secret:
            env[signing.ENV_SECRET] = secret
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def response(status=200):
        response = MagicMock()
        response.status_code = status
        return response

    def deliver(self):
        return self.env["chatwoot.bridge.event"]._cron_deliver()

    @contextmanager
    def posts(self, status=200):
        """Mock the HTTP post, run the delivery cron after the block, yield its calls."""
        calls = []
        with patch("requests.post", return_value=self.response(status)) as post:
            yield calls
            self.deliver()
            calls.extend(post.call_args_list)

    def process(self, raw):
        return self.env["mail.thread"].sudo().message_process(None, raw)
