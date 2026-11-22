# openoutreach

## Purpose

A read-only LinkedIn scout on the LinkedIn library of
[OpenOutreach](https://github.com/eracle/OpenOutreach), from
[openoutreach-scout](https://github.com/hoolia/openoutreach-scout). It runs one LinkedIn
account in a headed Chromium and turns ideal-customer-profile search hits and the
engagers of watched posts into signal rows in ClickHouse. It never connects or messages
unless both send switches are on; then it sends only messages a human approved in
Chatwoot, to leads in Odoo.

- Read-only by default, in code and on the network: every upstream send path raises and
  the browser aborts LinkedIn write requests.
- Human pacing with hard upper bounds, also enforced by `values.schema.json`. A
  checkpoint, captcha, login wall or warning page halts the scout for at least a day.
- One pod, `strategy: Recreate`, a kept volume for the browser profile and the pacing
  ledger, no Service and no service account token.

LinkedIn's User Agreement prohibits automated access; even a slow read-only scout can get
the account restricted. Read the upstream
[legal notice](https://github.com/eracle/OpenOutreach/blob/ade7fbdbd5a4595581fabf3cec1e803519a299a6/LEGAL_NOTICE.md)
before you install. The image is GPL-3.0-only; its source and both licence texts are in
the image under `/opt/upstream` and `/licenses`.

## Install

    helm repo add apps-openshift-eu https://apps.openshift.eu
    helm install openoutreach apps-openshift-eu/openoutreach

The release starts suspended (0 replicas) and read-only. Log in once (next section),
then set `suspend: false`.

| Value | Default | Meaning |
|---|---|---|
| `suspend` | `true` | `true` scales the Deployment to 0 |
| `mode` | `run` | `run` scouts in paced sessions; `bootstrap` opens the login page for a human on a localhost-only VNC display |
| `readOnly`, `sendEnabled` | `true`, `false` | Sending needs `readOnly: false` and `sendEnabled: true` |
| `playbook.linkedin.rules` | empty | Search keywords and watched posts; see the scout README, Configuration |
| `pacing.*` | the hard bounds | Each value may only narrow its bound: working days, window 08:30 to 18:00, 50 page views, 10 connects a day, 80 a week, 15 messages, 20 to 120 s between actions, 45 min sessions, 30 to 120 min pauses, 24 h rest after a halt |
| `timezone`, `locale` | `Europe/Amsterdam`, `nl-NL` | Time zone of the window and the caps; browser locale |
| `clickhouse.url`, `clickhouse.table` | empty, `signals.signal` | ClickHouse HTTP interface; empty writes the signals to `/data/signals.jsonl`. The scout does not create the table |
| `chatwoot.*`, `odoo.*` | empty | Send path only: Chatwoot URL, account, lead attribute and the excluded sender ids; Odoo URL and database |
| `secrets.existingSecret` | empty | Secret with any of `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `SLACK_WEBHOOK_URL`, `CHATWOOT_API_TOKEN`, `ODOO_USER`, `ODOO_PASSWORD` |
| `secrets.type` | `kubernetes` | `kubernetes` renders a Secret from `secrets.clickhouseUser` and the other values, only when one is set; `externalSecret` renders an ExternalSecret from `secrets.externalSecret.items` |
| `linkedinSession.existingSecret` | empty | Secret with key `storage_state.json`, a Playwright storage state that seeds an empty browser profile |
| `testRun.enabled` | `false` | One acceptance session outside the window: at most 5 page views and 10 minutes, its own `testRun.playbook`, never sends |
| `persistence.size`, `persistence.storageClassName` | `5Gi`, cluster default | Volume of the browser profile and the ledger |
| `imagePullSecrets` | empty | Pull Secrets for a private mirror of the image |

Nothing in this chart is generated: every credential comes from you, so a render without
cluster access (Argo CD) gives the same objects as `helm install`.

`helm uninstall` keeps the volume claim `<release>-data`, which holds the LinkedIn login.
Delete it by hand when the account is no longer used.

## How to login

There is no web interface and no admin user. The LinkedIn account owner logs in once in
the pod's browser, so the session starts from the cluster's egress address:

1. `helm upgrade` with `mode: bootstrap` and `suspend: false`; wait for the pod.
2. `oc port-forward deploy/<release> 5900:5900`. The VNC server listens on the pod's
   localhost only and has no password: only someone who may port-forward reaches it.
3. Open a VNC viewer on `localhost:5900` and log in to LinkedIn with the password and
   second factor. The log says `Logged in` when the feed shows.
4. `helm upgrade` with `mode: run`. `oc exec deploy/<release> -- oo-scout status` shows the
   caps used today and any halt.

After a halt, clear the cause in a bootstrap session if LinkedIn asks for it, then run
`oc exec deploy/<release> -- oo-scout resume`; the rest period still applies.

## Delivery instructions

- No managed Template: the scout automates one person's LinkedIn account against
  LinkedIn's terms, so it is installed on request for that person, not offered as
  self-service in the catalogue.
- The account owner does the login; never store the LinkedIn password in the cluster.
- With `clickhouse.url` set, create the signal table first (scout README, Signal table)
  and give the scout a ClickHouse user with INSERT on it.
- Alerts go to a Slack incoming webhook (`SLACK_WEBHOOK_URL`); without one, halts show
  only in the log.
- Before enabling the send path, create the Odoo lead fields `x_linkedin_profile`,
  `x_linkedin_sent_note_id` and `x_linkedin_message_sent`, and list the Chatwoot agent and
  bot users in `chatwoot.excludedSenderIds`.
