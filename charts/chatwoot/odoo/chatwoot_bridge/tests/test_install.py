from odoo.tests import tagged
from odoo.tools import convert_file

from ..hooks import MODULE, RECORDS, adopt_existing
from ..models.event import CRON_XMLID
from .common import BridgeCase

RULES = ["rule_ticket_created", "rule_lead_created", "rule_customer_mail", "rule_ticket_closed"]


@tagged("post_install", "-at_install")
class TestInstall(BridgeCase):

    def test_install_yields_rules_cron_and_fields(self):
        for xmlid in RULES:
            rule = self.env.ref(f"{MODULE}.{xmlid}")
            self.assertTrue(rule.active, xmlid)
            self.assertEqual(rule.action_server_ids.mapped("state"), ["code"], "rules only run code, never mail")
            self.assertIn("_chatwoot_bridge_notify", rule.action_server_ids.code)
        self.assertEqual(self.env.ref(f"{MODULE}.rule_customer_mail").filter_domain,
                         "[('model', 'in', ['helpdesk.ticket', 'crm.lead']), ('message_type', '=', 'email')]")
        cron = self.env.ref(CRON_XMLID)
        self.assertEqual((cron.code, cron.model_id.model), ("model._cron_deliver()", "chatwoot.bridge.event"))
        for model, name in [("helpdesk.ticket", "x_jira_key"), ("helpdesk.ticket", "x_chatwoot_url"),
                            ("crm.lead", "x_jira_key"), ("crm.lead", "x_chatwoot_url"),
                            ("mail.message", "x_chatwoot_message_id")]:
            self.assertIn(name, self.env[model]._fields, f"{model}.{name}")

    def test_module_owns_no_organisation(self):
        owned = self.env["ir.model.data"].search([("module", "=", MODULE)])
        kinds = set(owned.mapped("model"))
        for kind in ("helpdesk.team", "helpdesk.stage", "crm.team", "crm.stage", "mail.alias", "mail.alias.domain",
                     "fetchmail.server", "ir.mail_server", "mail.template", "res.company"):
            self.assertNotIn(kind, kinds, kind)
        crons = owned.filtered(lambda d: d.model == "ir.cron")
        self.assertEqual(["%s.%s" % (d.module, d.name) for d in crons], [CRON_XMLID])

    def test_upgrade_keeps_disabled_rules_and_cron_disabled(self):
        rule = self.env.ref(f"{MODULE}.rule_ticket_created")
        cron = self.env.ref(CRON_XMLID)
        rule.active = False
        cron.active = False
        for filename in ("data/automation.xml", "data/cron.xml"):
            convert_file(self.env, MODULE, filename, {}, mode="update")
        self.assertFalse(rule.active)
        self.assertFalse(cron.active)


@tagged("post_install", "-at_install")
class TestAdopt(BridgeCase):
    """A database with the bridge records of an earlier module under the same names."""

    def _forget(self):
        """Make the installed records look like another module's: drop this module's xmlids."""
        records = {x: self.env.ref(f"{MODULE}.{x}") for x in RECORDS}
        self.env["ir.model.data"].search([("module", "=", MODULE), ("name", "in", list(RECORDS))]).unlink()
        return records

    def test_existing_records_are_adopted_not_duplicated(self):
        before = self._forget()
        rule = before["rule_ticket_created"]
        rule.action_server_ids.code = "records._old_name_of_the_notify_method()"
        counts = {m: self.env[m].with_context(active_test=False).search_count([])
                  for m in ("base.automation", "ir.actions.server", "ir.cron")}
        adopt_existing(self.env)
        for xmlid, record in before.items():
            self.assertEqual(self.env.ref(f"{MODULE}.{xmlid}"), record, xmlid)
        # the install then loads the data files over the adopted records
        for filename in ("data/automation.xml", "data/cron.xml"):
            convert_file(self.env, MODULE, filename, {}, mode="init")
        after = {m: self.env[m].with_context(active_test=False).search_count([])
                 for m in ("base.automation", "ir.actions.server", "ir.cron")}
        self.assertEqual(counts, after, "no second rule, action or cron")
        self.assertEqual(rule.action_server_ids.code, "records._chatwoot_bridge_notify()", "the code is the module's")

    def test_adopt_leaves_unrelated_records_alone(self):
        self._forget()
        for record in self.env["base.automation"].search([("name", "like", "Chatwoot bridge:")]):
            record.name = "Something else: " + record.name
        adopt_existing(self.env)
        for xmlid in RULES:
            self.assertFalse(self.env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False), xmlid)
