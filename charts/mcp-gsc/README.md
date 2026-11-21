# mcp-gsc

## Purpose

Makes the Google Search Console data of your websites available to the agents of your project: search performance (queries, pages, clicks, impressions, positions), URL inspection, indexing status and sitemaps. The chart runs [mcp-gsc](https://github.com/AminForou/mcp-gsc) with one Google service account for the whole team; the key stays in the pod.

- The server speaks MCP over SSE on port 8000, path `/sse`. The chart writes an `AgentgatewayBackend` for it: an MCP gateway that generates endpoints from backends serves it as `/mcp/<namespace>/<name>` and writes a kagent `RemoteMCPServer` next to it. Without such a gateway, `remoteMCPServer.enabled` writes the `RemoteMCPServer` from the chart.
- Read-only by default: the tools that add or delete properties and sitemaps need `gsc.allowDestructive`.
- Upstream publishes no image: the pod installs the pinned PyPI release `gsc.package` `gsc.version` with uv at every start, which takes about half a minute and needs access to PyPI.
- The backend and the `RemoteMCPServer` appear once a key source is set, so an agent never gets a server without a credential. Until the key Secret exists the server starts and every tool reports the missing credential.
- A NetworkPolicy admits callers from the own project and `networkPolicy.ingressNamespaces`; the server reaches DNS and the public internet only.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install gsc grncloud/mcp-gsc --set-file secrets.serviceAccountJson=key.json
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects, the backend and the tool prefix; empty: `mcp.server` (`gsc`).
- `gsc.package`, `gsc.version` (default: `mcp-search-console`, `0.4.2`): PyPI release of the server.
- `gsc.allowDestructive` (default: false): Tools that add and delete properties and sitemaps.
- `gsc.dataState` (default: `all`): `final` returns finalized data only.
- `gsc.extraEnv` (default: `{}`): Further server settings.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret `<name>-credentials` from `secrets.serviceAccountJson` (empty: no Secret); `externalSecret` reads the JSON key from field `secrets.externalSecret.property` (default `notes`) of vault item `secrets.externalSecret.item` (default `<release>-<name>`) in `secrets.externalSecret.storeName`.
- `secrets.existingSecret`, `secrets.existingSecretKey` (default: empty, `credentials.json`): Use an existing Secret instead.
- `agentgatewayBackend.enabled` (default: true), `agentgatewayBackend.labels`: The backend, and extra labels on it such as the gateway's exposure label.
- `remoteMCPServer.enabled` (default: false), `remoteMCPServer.url`, `remoteMCPServer.protocol` (default: `STREAMABLE_HTTP`), `remoteMCPServer.allowedNamespaces` (default: `Same`): A kagent `RemoteMCPServer` at that address; `SSE` with `http://<name>.<namespace>:8000/sse` points it at the server itself.
- `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `mcp.openshift.eu`, `gsc`, `market-research`, `marketing`): Catalog labels.
- `podLabels`, `service.labels`, `service.annotations`: Extra labels and annotations.
- `resources` (default: requests 50m CPU and 192Mi, limits 1 CPU and 768Mi).
- `networkPolicy.ingressNamespaces` (default: empty): Label selectors of further namespaces that may call the server, for example the gateway's.

## How to login

No human login. The server reads Search Console as the Google service account in the key; agents authenticate to the MCP gateway with their own identity.

## Delivery instructions

1. In Google Cloud, create a service account in a project with the Search Console API enabled, and a JSON key for it.
2. In Search Console, add the service account's email address as a user of each property (Restricted is enough for the read-only tools).
3. Put the JSON key into `secrets.serviceAccountJson`, or into the notes of a vault item for `secrets.type: externalSecret`, and install the chart into the project of the agents.
4. Check: `oc get pods -l app.kubernetes.io/name=mcp-gsc` is Ready, and through the gateway the tool `<name>_list_properties` lists your properties.
