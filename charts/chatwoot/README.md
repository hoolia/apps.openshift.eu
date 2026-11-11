# Chatwoot

## Purpose

Chatwoot Community Edition: one screen for customer conversations, with the live chat
widget for a website and API channel inboxes that other systems fill. The chart runs the
upstream Community Edition image (no enterprise code) with a PostgreSQL database with
pgvector from the CloudNativePG operator, a Valkey store on a volume, and attachments on a
volume or in an object bucket. With single sign-on, staff log in at an OIDC provider and
never use a Chatwoot password.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install chatwoot grncloud/chatwoot

Install one release per project. The first start takes a few minutes: the database is
created, a Job migrates it and creates the account and its super admin, and web and
worker start only after that Job closed the onboarding page. There is no signup and no
onboarding page. `helm install --wait` works; give it `--timeout 15m` for the first start.

    oc get route chatwoot -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret chatwoot-credentials -o jsonpath='{.data.super-admin-password}' | base64 -d

The super admin is the break-glass account `superadmin@chatwoot.invalid` (value
`account.adminEmail`, always under the reserved domain `.invalid`), with the password
above. Without single sign-on it logs in at `/app/login` and invites the staff. With
single sign-on it logs in at `/super_admin` only: no login at the OIDC provider can become
a super admin.

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one and the pods read it from the Route. |
| `account.name` | `Support` | Name of the account the first install creates. |
| `account.adminEmail` | empty | Break-glass super admin, an address under `.invalid`. Empty: `superadmin@chatwoot.invalid`. |
| `attachments.storage` | `volume` | `volume`: a ReadWriteOnce volume, web and worker on one node, one web pod. `objectBucketClaim`: a bucket, any number of web pods. |
| `attachments.objectBucketClaim.storageClassName` | empty | Storage class of the bucket claim; required with `objectBucketClaim`. |
| `database.instances` | `1` | PostgreSQL instances. `2` adds a standby. |
| `database.imageName` | CloudNativePG `17.11-standard-trixie` | PostgreSQL image; it must carry pgvector. |
| `web.replicas`, `worker.replicas` | `1` | Pods of the web server and of the background worker. |
| `sso.enabled` | `false` | Staff log in at an OIDC provider through oauth2-proxy and the login bridge. |
| `sso.issuerUrl` | empty | OIDC issuer; required with `sso.enabled`. |
| `sso.clientId` | empty | OIDC client. Empty: the Route host. |
| `sso.allowedGroups` | empty | Groups (claim `sso.groupsClaim`) that may log in. Empty: every user of the issuer. |
| `sso.adminGroups` | empty | Groups whose members are account administrators, others agents. Empty: new users are agents and roles set in Chatwoot stay. |
| `sso.session.refresh` | `5m` | oauth2-proxy checks the user's groups at the OIDC provider again this often. |
| `sso.session.expire` | `12h` | An oauth2-proxy session ends after this. |
| `sso.session.chatwootHours` | `36` | A Chatwoot dashboard session ends after this many hours; 25 to 720. The dashboard keeps its session cookie for whole days rounded down, so a shorter value would end every session at once; the chart refuses it. |
| `sso.public.widget` | `true` | The live chat widget is reachable without a login. |
| `sso.public.inboxApi`, `.surveys`, `.helpCenter` | `false` | Further paths without a login. |
| `networkPolicy.webIngressFrom` | empty | Further peers that may reach Chatwoot web on port 3000. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `extraEnv` | empty | Further Chatwoot settings, for example the `SMTP_*` variables. |

### Single sign-on

With `sso.enabled` the Route points at oauth2-proxy. Every path needs a login at the OIDC
provider, except the ones the widget needs: `/packs/js/sdk.js`, `/widget`, `/vite/assets/`,
`/api/v1/widget/`, `/cable`, the sounds under `/audio/` and images under `/assets/images/`,
and reading attachments under
`/rails/active_storage/`. The dashboard's login page goes to the login bridge in the
oauth2-proxy pod, which listens on 127.0.0.1 only. It finds or creates the Chatwoot user
of the logged-in email through the Platform API, puts it in the account, and sends the
browser to that user's one-time login link (valid five minutes, usable once). Users get a
random Chatwoot password that nobody learns. The Platform App and its token are made by
the Job; the token is in Secret `chatwoot-platform`, which only the bridge and the Job
mount.

The OIDC client needs the redirect URI `https://<hostname>/oauth2/callback` and a secret
equal to key `client-secret` of Secret `chatwoot-oauth`. Its client id is the hostname
unless `sso.clientId` is set. The bridge never logs in an address under `.invalid` or a
super admin: Chatwoot's Platform API refuses those in this chart.

### When a user leaves the allowed group

| Access | Ends |
|---|---|
| Dashboard and API through the Route | at the next session refresh, within `sso.session.refresh` |
| A Chatwoot session token used from inside the cluster, past oauth2-proxy | when it expires, within `sso.session.chatwootHours` |
| Live updates over `/cable`, which is public for the widget | only when the user is removed from the account |
| Membership of the account | only by hand |

The bridge sees logins only, so it cannot notice that a user left a group. Delete the
user when they leave: Settings, Agents, delete. That removes them from the account and,
when they are in no other account, deletes the user with all its sessions. Then restart
the web pods (`oc rollout restart deployment/chatwoot-web`), which ends their open live
update connection.

### Network

The chart's NetworkPolicies admit the paths between its own components, but they add to
the platform's policies and cannot narrow them. A platform policy that admits every pod of
the same tenant, or of the same project, still reaches Chatwoot web on port 3000, the store
on port 6379 and oauth2-proxy on port 4180 directly. Such a pod meets Chatwoot's own
password logins (`/auth/sign_in`, `/super_admin`), never the single sign-on; every password
is random and unknown. The login bridge has no network port at all, so no pod can send it
an identity header.

### Mail

Chatwoot sends no mail unless `extraEnv` sets `SMTP_ADDRESS` and the other `SMTP_*`
variables. Without them password reset and notification mails are switched off.

### Interfaces for integrations

Service `chatwoot-web` port 3000 serves the Chatwoot API (`/api/v1/accounts/...` with a
user or bot access token) inside the project. A component in another project needs
`networkPolicy.webIngressFrom`. `odoo.enabled` and `draftAgent.enabled` are reserved for
later versions of the chart and stop the render when set.

`helm uninstall` deletes the database and the store, and keeps the attachment volume or
bucket.

## How to login

- **Single sign-on**: with `sso.enabled`, staff open `https://<hostname>/` and log in at
  the OIDC provider `sso.issuerUrl`. A member of `sso.allowedGroups` becomes a Chatwoot
  user of the account on the first login; a member of `sso.adminGroups` becomes an
  administrator, everyone else an agent. With `sso.adminGroups` empty, new users are agents
  and roles set in Chatwoot stay. The managed version (Template `chatwoot-managed`)
  switches it on.
- **Admin user**: the break-glass super admin `superadmin@chatwoot.invalid` (value
  `account.adminEmail`), password in Secret `chatwoot-credentials`, key
  `super-admin-password`. It logs in at `/super_admin` with single sign-on, and at
  `/app/login` without it:

    oc -n <project> extract secret/chatwoot-credentials --keys=super-admin-password --to=-

## Delivery instructions

- The chart needs the CloudNativePG operator on the cluster, version 1.26 or later. The
  platform team installs it and can add scheduled database backups to object storage.
- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- `attachments.storage: objectBucketClaim` needs an object bucket storage class from the
  platform team. Chatwoot serves the files itself, so the bucket needs no public address.
- For single sign-on, ask the platform team for the OIDC issuer and a client as described
  above, or register one at your own provider.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store. The super admin
  password needs an upper and a lower case letter, a digit and a sign.
