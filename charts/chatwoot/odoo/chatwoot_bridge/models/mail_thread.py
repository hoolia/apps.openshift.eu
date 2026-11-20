from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _get_message_create_valid_field_names(self):
        # the bridge posts a Chatwoot reply with message_post(x_chatwoot_message_id=...)
        return super()._get_message_create_valid_field_names() | {"x_chatwoot_message_id"}
