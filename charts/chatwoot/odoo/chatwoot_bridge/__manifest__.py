{
    "name": "Chatwoot bridge",
    "summary": "Signed events of tickets, leads and customer mail to the Odoo bridge of the chatwoot chart",
    "description": """
Odoo stays the record of every ticket, lead and mail; Chatwoot is the screen staff answer
in. This module is the Odoo side of the chart's Odoo bridge: link fields on tickets, leads
and messages, automation rules that queue an event carrying record ids only when a ticket
or lead is created, a customer mail arrives on one or a ticket reaches a folded stage, and
a cron that delivers each event HMAC-SHA256 signed, with retries. It creates no team,
alias, stage or mail server and sends no mail.

Settings: system parameter chatwoot_bridge.url (https://<chatwoot host>/odoo/events) and
the event secret in the environment of the Odoo process: CHATWOOT_BRIDGE_SECRET_FILE (a
file mounted from a Secret) or CHATWOOT_BRIDGE_SECRET.
""",
    "version": "19.0.1.0.0",
    "category": "Services/Helpdesk",
    "author": "apps.openshift.eu",
    "license": "LGPL-3",
    "depends": ["mail", "base_automation", "helpdesk", "crm"],
    "data": [
        "security/ir.model.access.csv",
        "data/automation.xml",
        "data/cron.xml",
        "views/event_views.xml",
    ],
    "pre_init_hook": "adopt_existing",
    "installable": True,
    "application": False,
    "auto_install": False,
}
