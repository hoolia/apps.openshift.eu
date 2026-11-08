# mcp-server

## Purpose

Runs one MCP server from any container image that serves streamable HTTP, for one team in your project, with its upstream credential taken from an existing Secret. It is meant to sit behind an MCP gateway, not on a public Route. Examples: `grafana/mcp-grafana`, `mcp-atlassian`, `mcp-server-odoo`.

Choices:

- Nothing renders until `image.repository` and `image.tag` are set; pin the tag.
- The Service port carries `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` (`path`) and `agentgateway.dev/mcp-target-name` (default: the release name), so an agentgateway backend with a label selector discovers it and prefixes its tools with the target name.
- `envFromSecret` names a Secret you create (for example from an ExternalSecret); its keys become environment variables. The chart never writes a credential.
- No ServiceAccount token in the pod; non-root, no capabilities, `RuntimeDefault` seccomp.
- A NetworkPolicy admits callers from the own project and `networkPolicy.ingressNamespaces` only. Egress goes to cluster DNS and the public internet; a private upstream needs an entry in `networkPolicy.egress.allow`.
- `rbacProxy.enabled` puts kube-rbac-proxy in front of the server: every call needs a Kubernetes ServiceAccount token allowed `services/proxy` on the Service (Role `<release>-caller`, subjects from `rbacProxy.callers`). The server then listens on `127.0.0.1`; `serviceAccountName` must name a ServiceAccount bound to `system:auth-delegator`, which the cluster administrator creates. With `route.enabled` a Route publishes the endpoint (reencrypt to the proxy) for an MCP gateway on another cluster.
- `secretEnv` maps single keys of existing Secrets to variables, for example a ServiceAccount token Secret.
- `remoteMCPServer.enabled` registers the server for the project's agents (kagent, Hermes) as a `RemoteMCPServer` at `https://<route.host><path>`. `remoteMCPServer.auth: oauth2` marks a server with its own OAuth broker, so each user logs in from the agent. Without `image.repository` the chart renders only this object, for a hosted MCP server at `remoteMCPServer.url`.
- `externalSecret.enabled` writes the Secret of `envFromSecret` from a secret store: one entry per variable in `externalSecret.data` (`secretKey`, `item`, `property`).
- The default port 8000 is outside the ports a typical project default opens to everyone (80, 443, 8080, 8443).

### Token check

`auth.enabled` (default: false) puts the token check of the library chart `mcp-auth` in
front of the server: an oauth2-proxy sidecar takes the Service port and the server listens
on `127.0.0.1:auth.upstreamPort` (default: 18000) only. Every request needs a bearer
JWT that `auth.issuerUrl` signed for one of `auth.audiences` (default: the release
namespace), else 401; a token whose groups claim names none of `auth.allowedGroups` gets
403. The caller's `Authorization` header reaches the server unchanged, streams pass as they
come, and `/.well-known/oauth-protected-resource` names the issuer without a token (RFC
9728). Without `auth.issuerUrl` every request but the health paths is refused. The README
of `mcp-auth` lists every `auth.*` value. The server must then listen on `127.0.0.1:auth.upstreamPort` (set `args` or `env` accordingly). `auth.enabled` and `rbacProxy.enabled` exclude each other: both take the Service port. Use `rbacProxy` for callers with Kubernetes ServiceAccount tokens and `auth` for callers with OpenID Connect tokens.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
oc create secret generic grafana-mcp --from-literal=GRAFANA_URL=https://grafana.example.com \
  --from-literal=GRAFANA_SERVICE_ACCOUNT_TOKEN=<token>
helm install grafana grncloud/mcp-server \
  --set image.repository=docker.io/grafana/mcp-grafana --set image.tag=2.0.2 \
  --set 'args={-t,streamable-http,--address,0.0.0.0:8000,--allowed-hosts,*,--disable-write}' \
  --set envFromSecret=grafana-mcp
```

A server that keeps state of its own (OAuth logins of its users, tokens, a SQLite file) needs `persistence.enabled`: a claim `<release>-data` mounted at `persistence.mountPath` (default `/data`) that survives restarts and an uninstall. Point the server's data path at it, for example with `env`.

A server that needs PostgreSQL gets one with `postgres.enabled`: a CloudNativePG `Cluster` `<release>-postgres` in the release namespace (needs the CloudNativePG operator), 1 instance on `postgres.storage.size` (default 1Gi) of `postgres.storage.storageClass` (default: the cluster's default class), with requests of 50m CPU and 128Mi memory and a 512Mi limit (`postgres.resources`). The server reads the connection URL, `postgresql://<owner>:<password>@<release>-postgres-rw.<namespace>:5432/<database>`, from the variable `postgres.envName` (default `DATABASE_URL`), taken from the key `uri` of the Secret `<release>-postgres-app` that the operator writes. A NetworkPolicy `<release>-postgres` admits the server on 5432, the instances among each other and the operator namespaces of `postgres.operatorNamespaceSelector` on 8000. The chart renders no backup: add a `ScheduledBackup` or let a platform policy add one.

```bash
helm install ledger grncloud/mcp-server \
  --set image.repository=ghcr.io/example/ledger-mcp --set image.tag=1.0.0 \
  --set postgres.enabled=true --set postgres.storage.size=5Gi
```

## How to login

No human login: callers authenticate with a bearer token checked by `auth.*`, or the OpenShift RBAC proxy `rbacProxy.*`.

## Delivery instructions

- Check the pod is Ready: `oc get pods -l app.kubernetes.io/instance=grafana`.
- With `postgres.enabled`, check the database is healthy: `oc get clusters.postgresql.cnpg.io <release>-postgres`.
- Through your MCP gateway the tools appear as `<release>_<tool>`. From inside the project: `POST http://<release>:8000/mcp` with an MCP `initialize` request.
- The server has whatever rights its credential has: give it a read-only one where the upstream allows it.
