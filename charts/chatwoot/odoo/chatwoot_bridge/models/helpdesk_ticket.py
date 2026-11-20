from odoo import models


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _inherit = ["helpdesk.ticket", "chatwoot.bridge.linked"]

    def _chatwoot_bridge_event(self):
        return "ticket.created"
