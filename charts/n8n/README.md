# n8n

## Purpose

n8n: workflow automation with a visual editor, several hundred integrations and AI
nodes. The chart runs the upstream image with a data volume and a PostgreSQL database of
the CloudNativePG operator. Queue mode, with separate workers and a Redis store, is an
option.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install n8n grncloud/n8n

Install one release per project. The first start takes a minute or two: the database is
created first and n8n restarts until it is there. Open the address and create the owner
account in the browser; n8n has no default password.

    oc get route n8n -o jsonpath='https://{.spec.host}{"\n"}'

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one. |
| `persistence.size` | `5Gi` | Size of the data volume. |
| `timezone` | `Europe/Amsterdam` | Time zone of schedules. |
| `database.instances` | `1` | PostgreSQL instances. `2` adds a standby. |
| `database.size` | `5Gi` | Size of the database volume of each instance. |
| `queue.enabled` | `false` | Queue mode: workers run the executions. |
| `queue.workers` | `1` | Number of workers in queue mode. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.encryptionKey` | empty | Key for stored credentials. Empty: generated. |
| `extraEnv` | empty | Further n8n settings as environment variables. |

n8n must know its own address for webhook and OAuth callback addresses. With
`route.hostname` set, the chart uses that name. With an empty hostname the pod reads the
host that OpenShift assigned to the Route at every start; for that the chart adds a
ServiceAccount that may read this one Route.

The encryption key in the Secret `n8n-credentials` encrypts every credential you store
in n8n. Keep a copy of it: without the key the stored credentials are lost. It is
generated at the first install and kept on every upgrade with `helm upgrade`. A tool that
renders the chart without access to the cluster, such as Argo CD or `helm template`,
generates a new value on every render: set `secrets.encryptionKey` and, in queue mode,
`secrets.redisPassword` there.

The database user and password come from the Secret `n8n-db-app` that the operator
creates. The default is one n8n pod that also runs the executions, which fits most
installs. Switch on queue mode when executions are heavy or many: it adds one pod per
worker and a small store for the queue.

`helm uninstall` deletes the database and the data volume. Export your workflows first.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- The chart needs the CloudNativePG operator on the cluster. The platform team installs
  it and can add scheduled database backups to object storage.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
