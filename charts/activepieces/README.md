# activepieces

## Purpose

Activepieces: no-code workflow automation with hundreds of integrations ("pieces"), AI
agents and an MCP server that lets AI clients run your flows and piece actions. The
chart runs the upstream community edition image, API and worker in one pod, with a
PostgreSQL database of the CloudNativePG operator and a Valkey job queue. A scheduled
database backup is an option.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install activepieces grncloud/activepieces --set route.hostname=flows.example.com

Install one release per project; `route.hostname` is required. The first start takes a
few minutes: the database is created and migrated, and the piece catalogue is synced.

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | required | Public hostname. Webhooks, OAuth callbacks and the worker's piece downloads use it. |
| `executionMode` | `SANDBOX_CODE_ONLY` | Code steps run isolated in V8; npm imports in code steps fail. `UNSANDBOXED` runs them in the worker with its environment. |
| `workerConcurrency` | `5` | Flow runs the worker processes at once. |
| `executionDataRetentionDays` | `30` | Days run data, logs and events are kept. |
| `cache.sizeLimit` | `5Gi` | Piece and code cache; it is filled again after a restart. |
| `database.instances` | `1` | PostgreSQL instances. `2` adds a standby. |
| `database.size` | `5Gi` | Size of the database volume of each instance. |
| `database.backup.enabled` | `false` | Daily backup through the barman-cloud plugin. |
| `database.backup.objectStoreName` | empty | barman-cloud `ObjectStore` in this project; required for a backup. |
| `queue.size` | `1Gi` | Volume of the Valkey job queue. |
| `secrets.existingSecret` | empty | Secret with the keys `encryption-key` (32 hexadecimal characters), `jwt-secret` and `queue-password`. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `extraEnv` | empty | Further Activepieces settings, for example `AP_SMTP_HOST` and the other `AP_SMTP_*` variables for invitation mail. |

The key `encryption-key` in the Secret `activepieces` encrypts the stored connections
of your flows. Keep a copy of it: without the key Activepieces cannot read them. It is
generated at the first install and kept on every `helm upgrade`. A tool that renders
the chart without access to the cluster, such as Argo CD, generates a new value on every
render: set `secrets.encryptionKey`, `secrets.jwtSecret` and `secrets.queuePassword`, or
`secrets.existingSecret`. An encryption key that is not 32 hexadecimal characters is
hashed to 32.

`helm uninstall` deletes the database with every flow, connection and run.

## How to login

- **Admin user**: Activepieces has no default password. Open `https://<hostname>` and
  sign up: the first account becomes the platform admin. Invite the others from
  Platform Admin, Users.
- **MCP**: an AI client connects to `https://<hostname>/mcp` and signs in through the
  same browser login. The MCP settings page of a project shows the address.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- The chart needs the CloudNativePG operator on the cluster. For a backup the platform
  team or you create a barman-cloud `ObjectStore` and name it in
  `database.backup.objectStoreName`.
- Invitation and password mail need an SMTP server: set it in Platform Admin, or pass the
  `AP_SMTP_*` variables in `extraEnv`.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
