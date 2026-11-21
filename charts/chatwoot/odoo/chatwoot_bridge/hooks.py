"""Install hook: adopt bridge records an earlier module made under the same names.

A database that ran the bridge before this module existed already has the automation
rules, their server actions and the delivery cron, under the names this module uses. Each
such record gets this module's xmlid before the data files load, so the install updates
it (model, code) instead of creating a second rule that would queue every event twice.
"""
import logging

_logger = logging.getLogger(__name__)

MODULE = "chatwoot_bridge"
# xmlid: (model, name, model the record acts on); names as in data/*.xml
RECORDS = {
    "rule_ticket_created": ("base.automation", "Chatwoot bridge: ticket created", "helpdesk.ticket"),
    "action_ticket_created": ("ir.actions.server", "Chatwoot bridge: ticket created", "helpdesk.ticket"),
    "rule_lead_created": ("base.automation", "Chatwoot bridge: lead created", "crm.lead"),
    "action_lead_created": ("ir.actions.server", "Chatwoot bridge: lead created", "crm.lead"),
    "rule_customer_mail": ("base.automation", "Chatwoot bridge: customer mail on ticket or lead", "mail.message"),
    "action_customer_mail": ("ir.actions.server", "Chatwoot bridge: customer mail on ticket or lead", "mail.message"),
    "rule_ticket_closed": ("base.automation", "Chatwoot bridge: ticket closed", "helpdesk.ticket"),
    "action_ticket_closed": ("ir.actions.server", "Chatwoot bridge: ticket closed", "helpdesk.ticket"),
    "cron_deliver_events": ("ir.cron", "Chatwoot bridge: deliver events", None),
}


def _find(env, model, name, on_model):
    domain = [("name", "=", name)]
    if on_model:
        domain.append(("model_id.model", "=", on_model))
    if model == "ir.actions.server":
        # a rule's action, not the action behind a cron of the same name
        domain.append(("usage", "=", "base_automation"))
    return env[model].with_context(active_test=False).search(domain, order="id", limit=1)


def adopt_existing(env):
    """pre_init: give an existing record of the same name this module's xmlid (noupdate)."""
    Data = env["ir.model.data"]
    for xmlid, (model, name, on_model) in RECORDS.items():
        if env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False):
            continue
        record = _find(env, model, name, on_model)
        if not record:
            continue
        Data.create({"module": MODULE, "name": xmlid, "model": model, "res_id": record.id, "noupdate": True})
        _logger.info("chatwoot_bridge: adopted %s %s (%s)", model, record.id, name)
