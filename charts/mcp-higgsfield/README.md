# mcp-higgsfield

## Purpose

Makes Higgsfield AI image and video generation available to the agents of your project: Soul, Reve and Seedream images, Kling, Seedance and DoP videos, talking heads, reusable characters and generation status. The chart runs the npm package [higgsfield-mcp](https://github.com/Storyvord/higgsfield-mcp) with the team's Higgsfield API key; the key stays in the pod.

- The server speaks MCP over stdio. A kagent `MCPServer` runs it behind a streamable HTTP endpoint: kagent's controller writes a Deployment and a Service `<name>:3000` of the same name. The chart needs kagent's `MCPServer` API (kagent.dev/v1alpha1) in the cluster.
- The chart writes an `AgentgatewayBackend` for that Service: an MCP gateway that generates endpoints from backends serves it as `/mcp/<namespace>/<name>` and writes a kagent `RemoteMCPServer` next to it. Without such a gateway, `remoteMCPServer.enabled` writes the `RemoteMCPServer` from the chart.
- `npx` installs the pinned release `higgsfield.package` `higgsfield.version` at every start and needs access to the npm registry.
- Without a key the server starts and lists its tools; every generation fails until a key is set. Every generation spends the account's Higgsfield credits.
- A NetworkPolicy admits callers from the own project and `networkPolicy.ingressNamespaces`; the server reaches DNS and the public internet only.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install higgsfield grncloud/mcp-higgsfield \
  --set secrets.apiKey=<API key> --set secrets.apiSecret=<API secret>
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects, the backend and the tool prefix; empty: `mcp.server` (`higgsfield`).
- `higgsfield.package`, `higgsfield.version` (default: `higgsfield-mcp`, `0.2.0`): npm release of the server.
- `higgsfield.extraEnv` (default: `{}`): Further server settings.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret `<name>-api-key` from `secrets.apiKey` and `secrets.apiSecret` (both empty: no Secret); `externalSecret` reads the key from `secrets.externalSecret.apiKeyProperty` (default `login.username`) and the secret from `secrets.externalSecret.apiSecretProperty` (default `login.password`) of vault item `secrets.externalSecret.item` (default `<release>-<name>`) in `secrets.externalSecret.storeName`.
- `secrets.existingSecret` (default: empty): Existing Secret with the keys `HF_API_KEY` and `HF_SECRET`.
- `timeout` (default: `30s`): Longest wait of the MCPServer for one call.
- `agentgatewayBackend.enabled` (default: true), `agentgatewayBackend.labels`: The backend, and extra labels on it such as the gateway's exposure label.
- `remoteMCPServer.enabled` (default: false), `remoteMCPServer.url` (default: `http://<name>.<namespace>:3000/mcp`), `remoteMCPServer.allowedNamespaces` (default: `Same`): A kagent `RemoteMCPServer`.
- `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `mcp.openshift.eu`, `higgsfield`, `higgsfield`, `marketing`): Catalog labels.
- `resources` (default: requests 20m CPU and 128Mi, limits 1 CPU and 512Mi).
- `networkPolicy.ingressNamespaces` (default: empty): Label selectors of further namespaces that may call the server, for example the gateway's.

## How to login

No human login. The server calls Higgsfield with the team's API key; agents authenticate to the MCP gateway with their own identity.

## Delivery instructions

1. Create an API key and secret at https://cloud.higgsfield.ai/api-keys on an account with credits.
2. Put them into `secrets.apiKey` and `secrets.apiSecret`, or into a vault item (username: key, password: secret) for `secrets.type: externalSecret`, and install the chart into the project of the agents.
3. Check: `oc get mcpserver <name>` is Ready, and through the gateway the tools `<name>_*` are listed.

Every agent of the project spends the account's credits with the same key: keep an eye on the usage page of Higgsfield.
