from odoo import fields, models
from odoo.tools import email_normalize

BRIDGED_MODELS = ("helpdesk.ticket", "crm.lead")


class MailMessage(models.Model):
    _inherit = "mail.message"

    x_chatwoot_message_id = fields.Char("Chatwoot message", copy=False, readonly=True,
                                        help="Set by the Odoo bridge on a message it posted from Chatwoot")

    # a redelivered Chatwoot reply cannot become a second message
    _x_chatwoot_message_id_unique = models.UniqueIndex("(x_chatwoot_message_id) WHERE x_chatwoot_message_id IS NOT NULL")

    def _chatwoot_own_addresses(self):
        """Addresses this Odoo sends from or receives on; a mail from one is never a customer's."""
        icp = self.env["ir.config_parameter"].sudo()
        domain = icp.get_param("mail.catchall.domain")
        own = {email_normalize(icp.get_param("mail.default.from") or "")}
        if domain:
            for key in ("mail.catchall.alias", "mail.bounce.alias"):
                if icp.get_param(key):
                    own.add(email_normalize("%s@%s" % (icp.get_param(key), domain)))
        own.update(email_normalize(e) for e in self.env["res.company"].sudo().search([]).mapped("email") if e)
        own.discard(False)
        own.discard(None)
        return own

    def _chatwoot_is_customer_mail(self):
        """An incoming mail on a ticket or lead, written by someone outside the company.

        Staff partners (an internal user) and Odoo's own addresses are excluded, and so is
        any message the bridge wrote itself, which is how a reply never echoes back.
        """
        self.ensure_one()
        msg = self.sudo()
        if msg.model not in BRIDGED_MODELS or not msg.res_id:
            return False
        if msg.message_type != "email" or msg.x_chatwoot_message_id:
            return False
        if msg.author_id and not msg.author_id.partner_share:
            return False
        sender = email_normalize(msg.email_from or "")
        if not sender:
            return False
        if sender in self._chatwoot_own_addresses():
            return False
        if self.env["mail.alias.domain"].sudo()._find_aliases([sender]):
            return False
        return True

    def _chatwoot_bridge_notify_customer_mail(self):
        Event = self.env["chatwoot.bridge.event"]
        sent = 0
        for message in self:
            if not message._chatwoot_is_customer_mail():
                continue
            record = self.env[message.model].browse(message.res_id)
            if record._chatwoot_bridge_active():
                sent += bool(Event._enqueue("message.created", record, message=message))
        return sent
