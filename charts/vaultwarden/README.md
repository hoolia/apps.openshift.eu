# Vaultwarden

## Purpose

Vaultwarden: a password manager server for the Bitwarden apps, browser extensions and
web vault. The chart runs the upstream image with one data volume. The database is SQLite
on that volume, so the release needs no database server.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install vaultwarden grncloud/vaultwarden

Install one release per project. Open the web vault and the admin page with:

    oc get route vaultwarden -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret vaultwarden-credentials -o jsonpath='{.data.admin-token}' | base64 -d

Registration is closed on a new instance. To create the first account:

1. Open `https://<host>/admin` and enter the admin token.
2. On the page Users, invite your mail address.
3. Open `https://<host>`, choose Create account and register with that address.

Invite every further user the same way, or let an organisation owner invite them. An
invited address can register without a mail server.

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one. |
| `persistence.size` | `5Gi` | Size of the data volume. |
| `signups.allowed` | `false` | `true` lets everyone who reaches the address register. |
| `signups.domains` | empty | Mail domains that may register without an invitation. |
| `smtp.host` | empty | Mail server for invitations, verification and password hints. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.adminToken` | empty | Token of the admin page. Empty: generated. |
| `extraEnv` | empty | Further Vaultwarden settings as environment variables. |

Vaultwarden must know its own address (`DOMAIN`) for passkeys and for links in mails.
With `route.hostname` set, the chart uses that name. With an empty hostname the pod reads
the host that OpenShift assigned to the Route at every start; for that the chart adds a
ServiceAccount that may read this one Route.

The generated admin token is a random string of 48 characters, stored as plain text in
the Secret. Vaultwarden logs a notice about that. For a hashed token, run
`vaultwarden hash` in the pod and set the Argon2 string it prints as
`secrets.adminToken`; you then log in with the password you typed.

The token is generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, generates a new value on every render: set the token there.

The data volume holds every vault. Back it up, and keep the volume when you remove the
release: `helm uninstall` deletes it.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- Outgoing mail needs a mail server that accepts the sender address. Ask the platform
  team for a mailbox or a relay when you have none.
- Single sign-on needs a client at the identity provider. Ask the platform team for the
  client, then set the `SSO_*` settings of Vaultwarden with `extraEnv`.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
