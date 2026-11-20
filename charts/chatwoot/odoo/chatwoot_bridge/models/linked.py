from odoo import fields, models


class ChatwootBridgeLinked(models.AbstractModel):
    """A record mirrored to a Chatwoot conversation: helpdesk.ticket and crm.lead.

    The field names start with x_ so a database that had them as custom fields keeps its
    data; the bridge writes x_chatwoot_url, staff write x_jira_key.
    """
    _name = "chatwoot.bridge.linked"
    _description = "Record mirrored to a Chatwoot conversation"

    x_jira_key = fields.Char("Issue key", copy=False,
                             help="Key of a linked issue in the issue tracker, for example PROJ-123; the Chatwoot "
                                  "side panel shows it with its status")
    x_chatwoot_url = fields.Char("Chatwoot conversation", copy=False, readonly=True,
                                 help="Set by the Odoo bridge when it links the record to a conversation")

    def _chatwoot_bridge_event(self):
        """The event name the creation of a record of this model sends."""
        raise NotImplementedError

    def _chatwoot_bridge_active(self):
        """Whether records of this model are mirrored now; override to switch a model off."""
        return True

    def _chatwoot_bridge_notify(self, event=None):
        """Queue one event per record; returns the number queued.

        A record the bridge creates itself (context chatwoot_bridge_no_event) sends no
        creation event: the bridge links it to its conversation.
        """
        if not self or not self._chatwoot_bridge_active():
            return 0
        if event is None and self.env.context.get("chatwoot_bridge_no_event"):
            return 0
        Event = self.env["chatwoot.bridge.event"]
        sent = 0
        for record in self:
            name = event or record._chatwoot_bridge_event()
            sent += bool(Event._enqueue(name, record, repeatable=name == "ticket.closed"))
        return sent
