from odoo import models


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "chatwoot.bridge.linked"]

    def _chatwoot_bridge_event(self):
        return "lead.created"
