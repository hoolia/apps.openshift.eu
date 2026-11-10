# clickhouse

## Purpose

ClickHouse is a column-oriented database for analytics: it answers aggregate queries over
millions of rows in milliseconds. The chart runs one upstream ClickHouse server in the
release namespace with a data volume. Clients in the same project, and in namespaces you
allow, reach it on port 8123 (HTTP) and 9000 (native); a public address is optional.

The server has three users, each with its own Secret `<fullname>-<role>`:

| Role | User | Rights | Used by |
|---|---|---|---|
| `admin` | `admin` | everything, including users and grants | the team's database administrator |
| `reader` | `reader` | `SELECT` and `SHOW` on every database, read-only profile | the MCP tools, reporting clients |
| `writer` | `writer` | create, alter, drop and fill databases and tables | an ingestion tool such as Airbyte |

`<fullname>` is the release name, with `-clickhouse` appended unless the release name
already contains `clickhouse`. The built-in user `default` is removed.

The chart also runs the official [mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse)
next to the server: read-only SQL tools for AI agents (`run_query`, `list_databases`,
`list_tables`) on streamable HTTP, logged in as the reader. Its Service carries
`appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and
`agentgateway.dev/mcp-target-name`, so an MCP gateway that discovers Services by label
publishes the tools to the project's agents as `<targetName>_<tool>`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install clickhouse grncloud/clickhouse
```

- `persistence.size` (default: 10Gi), `persistence.storageClass` (default: empty, the
  cluster default): the data volume.
- `resources` (default: requests 500m CPU and 2Gi memory, limits 2 CPU and 4Gi): the
  server sizes its caches and its memory limit from the container limit.
- `users.<role>.name`: user names. `users.reader.grants`, `users.writer.grants`: one
  `GRANT` statement each. `users.reader.settings`: settings of the reader profile
  (default: `readonly` 2, `max_execution_time` 120 seconds, `max_memory_usage` 2GiB).
- `database` (default: default): database the connection Secrets name.
- `config.markCacheSize` (default: 256MiB), `config.disableSystemLogs` (default: the six
  fastest-growing system log tables), `config.extra`: further server configuration XML.
- `secrets.type` (default: kubernetes): `externalSecret` reads the three passwords from a
  secret store.
- `secrets.adminPassword`, `secrets.readerPassword`, `secrets.writerPassword` (default:
  empty): generated at the first install and kept on upgrade.
- `networkPolicy.ingressNamespaces` (default: empty): label selectors of further namespaces
  that may connect to the database.
- `route.enabled` (default: false), `route.host`, `route.annotations`: publish the HTTP
  interface with edge TLS; put the cert-manager issuer annotations in `route.annotations`.
- `podLabels`, `mcp.podLabels`: extra pod labels, for example
  `sidecar.istio.io/inject: "false"` in a service mesh namespace.
- `mcp.enabled` (default: true): run the SQL tools.
- `mcp.targetName` (default: the release name): tool prefix on the gateway.
- `mcp.queryTimeout` (default: 60): seconds one query of a tool call may run.
- `mcp.ingressNamespaces` (default: empty): label selectors of the namespaces that may call
  the tools besides the own one, for example the gateway's.
- `mcp.allowedHosts` (default: empty): further `Host` header values the tool server
  answers; its Service names are always allowed.
- `mcp.extraEnv`: further mcp-clickhouse settings.

Every user Secret has the keys `username`, `password`, `password-sha256`, `host`, `port`
(HTTP, 8123), `native-port` (9000), `database` and `url`. An ingestion tool reads the
writer's:

```bash
oc extract secret/clickhouse-writer --keys=host,port,username,database --to=-
```

A tool that renders the chart without access to the cluster, such as Argo CD or
`helm template`, generates new passwords on every render: set the three passwords there,
or use `secrets.type=externalSecret`.

## How to login

- **Single sign-on**: none; ClickHouse has no web login.
- **Admin user**: user `admin`, password in Secret `<fullname>-admin`, key `password`:

      oc rsh <fullname>-0 clickhouse-client --user admin \
        --password "$(oc extract secret/<fullname>-admin --keys=password --to=-)"

## Delivery instructions

- Check the pods are Ready: `oc get pods -l app.kubernetes.io/instance=<release>`.
- Test the reader: `curl -u reader:<password> 'http://<fullname>:8123/?query=SELECT%201'`
  from a pod in the project answers `1`.
- Give the ingestion tool the Secret `<fullname>-writer` and the host
  `<fullname>.<namespace>.svc`, port 8123 (HTTP) or 9000 (native).
- `helm uninstall` keeps the data volume (a StatefulSet volume claim). Delete the claim
  `data-<fullname>-0` to remove the data.
- With `secrets.type=externalSecret`, create the vault items named in
  `secrets.externalSecret.items` (default `<fullname>-admin`, `<fullname>-reader`,
  `<fullname>-writer`) with the password in the password field, and set
  `secrets.externalSecret.storeName` to your store.
- The tool server asks callers for no login: anyone who reaches its port reads every table
  the reader can read. Keep `mcp.ingressNamespaces` to the gateway, and let the gateway
  authenticate callers.
