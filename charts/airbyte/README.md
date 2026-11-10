# Airbyte

## Purpose

Airbyte moves data from APIs and databases (ad platforms, CRMs, SaaS tools) into a
warehouse such as ClickHouse or PostgreSQL. This chart wraps the upstream chart `airbyte`
2.5.0 (Chart V2) and adds what a project on OpenShift needs:

- every pod runs under `restricted-v2` with OpenShift's own user id;
- the chart's own ServiceAccount, Role and RoleBinding, so a GitOps tool does not replace
  them on every sync (upstream renders them as install hooks);
- PostgreSQL and MinIO with passwords generated inside the cluster, never in values;
- a Route with a certificate from cert-manager for the web UI and the API;
- an MCP server for AI agents: list connections, start and cancel syncs, read job status;
- sources, destinations and connections declared in values and applied through the
  Airbyte API after every install and upgrade.

The upstream chart is included under the alias `upstream`; every upstream value is set
below `upstream:`, and its global values below `global:`. Sync, check and discover pods
are started by the workload launcher in the release namespace.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install airbyte grncloud/airbyte \
      --set installMode=helm \
      --set route.host=airbyte.example.org \
      --set global.airbyteUrl=https://airbyte.example.org

| Value | Default | Meaning |
|---|---|---|
| `route.host` | empty | Host of the web UI and the API. Empty: OpenShift assigns one. |
| `global.airbyteUrl` | empty | `https://<route.host>`. Airbyte's login cookie needs it. |
| `database.persistence.size` | `5Gi` | Size of the database volume. |
| `database.enabled` | `true` | `false`: an external PostgreSQL at `global.database.host`, credentials in Secret `global.database.secretName` (keys `DATABASE_USER`, `DATABASE_PASSWORD`). |
| `secrets.type` | `kubernetes` | `externalSecret` reads the database and MinIO credentials from the vault items `secrets.externalSecret.items.database` and `.minio` (login username and password) through store `secrets.externalSecret.storeName`; an empty item is generated in the cluster. |
| `secrets.minioRootPassword` | empty | MinIO root password. Empty: an install hook creates the Secret `airbyte-minio-credentials` with a random one, once. |
| `minio.persistence.size` | `5Gi` | Size of the volume for logs and state. |
| `global.jobs.resources` | 100m, 256Mi; limit 2 CPU, 2Gi | Requests and limits of each sync, check and discover container. |
| `upstream.worker.maxSyncWorkers` | upstream | Number of syncs that run at the same time. |
| `mcp.enabled` | `true` | Run the MCP server. |
| `mcp.allowWrite` | `true` | `false` leaves out the tools that start and cancel syncs. |
| `mcp.ingressNamespaces` | empty | Label selectors of the namespaces that may call the MCP server, for example an MCP gateway's. |
| `connections.enabled` | `false` | Apply `connections.*` through the Airbyte API after every install and upgrade. |
| `connections.externalSecrets` | `[]` | Connector credential Secrets from the secret store: `{name, item, data: {<key>: <item property>}}`. |
| `connections.definitionVersions` | `{}` | Connector versions by registry name, for example `ClickHouse: 2.1.34`. |
| `featureFlags` | stdio mode | Platform feature flags. |

Platform pods request about 0.75 CPU and 4.5Gi of memory; each running sync adds one pod
per connector with `global.jobs.resources`.

### Declared connections

    connections:
      enabled: true
      destinations:
      - name: warehouse
        definition: ClickHouse
        configuration:
          host: clickhouse.example.svc
          port: "8123"
          protocol: http
          database: default
          username: airbyte
          password: ${secret:clickhouse-writer/password}
      sources:
      - name: google-ads
        definition: Google Ads
        configuration:
          credentials:
            developer_token: ${secret:google-ads/developer-token}
      connections:
      - name: google-ads-to-warehouse
        source: google-ads
        destination: warehouse
        schedule: 0 0 2 * * ?
        namespaceFormat: google_ads

- `definition` is the connector's name in the Airbyte registry, or the name of an entry
  of `connections.declarativeSources` (a low-code connector manifest). The chart carries
  one manifest: `{name: Odoo, file: odoo}` reads Odoo 19 and later through the JSON-2 API
  (`url`, `database`, `api_key`; partners, leads, stages, sales orders and UTM campaigns,
  sources and mediums).
- `connections.reconcileSchedule` (a cron schedule) applies the declarations again, so a
  credential Secret created later takes effect without an upgrade.
- `${secret:<Secret>/<key>}` is replaced by that key of a Secret in the release
  namespace, `${secretlist:<Secret>/<key>}` by its comma-separated items as a list. A source or destination whose Secret or key does not exist is skipped, and so
  are its connections; add the Secret and upgrade, or rerun the job.
- Objects are matched by name. An entry removed from values stays in Airbyte; delete it
  in the UI.
- The Job `<release>-config` prints what it created, updated and skipped.

### MCP server

Service `<release>-mcp` serves streamable HTTP at port `mcp.port` and path `mcp.path`, with
`appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and
`agentgateway.dev/mcp-target-name`, so an MCP gateway that discovers servers by them
publishes the tools. Tools: `list_workspaces`, `list_connections`, `get_connection`,
`list_sources`, `list_destinations`, `list_jobs`, `get_job`, `trigger_sync`, `cancel_job`.
It calls the Airbyte API with the instance admin's API client.

## How to login

Open `https://<route.host>`. Airbyte has one user, the instance admin. On the first visit
the UI asks for an email address and an organization name; the password is in the Secret
`airbyte-auth-secrets`, key `instance-admin-password`, which the bootloader creates at the
first install:

    oc get secret airbyte-auth-secrets -o jsonpath='{.data.instance-admin-password}' | base64 -d

The same Secret holds `instance-admin-client-id` and `instance-admin-client-secret`, the API
client the MCP server and the configuration job use. There is no single sign-on in the
community edition.

## Delivery instructions

- One release per namespace: the upstream chart uses fixed object names.
- `installMode: argocd` (default): the database, its Secret and the bootloader account are
  normal objects in early sync waves and the bootloader is a Sync hook after them, so
  Argo CD never replaces the database. `installMode: helm` for `helm install`: they are
  install hooks, and `helm upgrade` replaces them; set
  `upstream.airbyteBootloader.annotations: {}` as well. Its data stays on the volume claim
  `airbyte-volume-db-airbyte-db-0`, the claim of upstream's bundled database. A `helm uninstall` or Application delete leaves these hook objects
  behind; delete them by hand.
- The database and MinIO passwords and the instance admin's password and API client are
  generated inside the cluster (Secrets `airbyte-db-credentials`,
  `airbyte-minio-credentials` and `airbyte-auth-secrets`), so a GitOps tool that renders
  without cluster access never rotates them. Uninstalling leaves the three Secrets.
- ClickHouse 26.8 and later need destination ClickHouse 2.1.33 or later
  (`connections.definitionVersions`).
- Keep `mcp.ingressNamespaces` to the gateway: the MCP server has no login of its own and
  can start syncs.
- Connector pods download their images from Docker Hub (`airbyte/source-*`).
