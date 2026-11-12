# mcp-hosted

## Purpose

Makes one vendor-hosted MCP server (Semrush, Ahrefs, Similarweb and the like) available to the agents of your project, with one API key for the whole team. Nothing runs in the project: the chart writes an `AgentgatewayBackend` with the vendor's endpoint and a Secret with the key. The project's MCP gateway serves the server as `/mcp/<namespace>/<server>` and adds the key to every request, so agents carry only their own identity and never see the key.

On a platform with an MCP gateway that generates endpoints from `AgentgatewayBackend` objects, the endpoint and a kagent `RemoteMCPServer` appear on their own. Without one, the backend does nothing.

Nothing is rendered until the endpoint host and a key source are set: an agent never gets a server that answers `Unauthorized`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install semrush grncloud/mcp-hosted \
  --set mcp.server=semrush --set endpoint.host=<vendor MCP host> --set endpoint.path=<path> \
  --set secrets.apiKey=<API key>
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the backend, the gateway path and the server in the agent UI; empty: `mcp.server`.
- `endpoint.host`, `endpoint.port`, `endpoint.path` (default: empty, 443, `/mcp`): The vendor's hosted MCP endpoint, always TLS.
- `endpoint.protocol` (default: `StreamableHTTP`): `StreamableHTTP` or `SSE`.
- `auth.header`, `auth.prefix` (default: `Authorization`, `Bearer `): Request header for the key and the text in front of it, for example `x-api-key` with an empty prefix.
- `auth.queryParameter` (default: empty): Send the key as this query parameter instead of a header.
- `mcp.labelDomain` (default: `mcp.openshift.eu`): Prefix of the selection labels.
- `mcp.server` (required): Server name and label value.
- `mcp.bundle`, `mcp.category` (default: empty): Bundle and category labels; empty: no label.
- `mcp.description`, `mcp.icon`: Description and card icon, as annotations for catalogs.
- `labels` (default: `{}`): Extra labels on the backend, for example the gateway's exposure label.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret from `secrets.apiKey` (empty: nothing is rendered); `externalSecret` reads the key from field `secrets.externalSecret.property` (default `login.password`) of vault item `secrets.externalSecret.item` (default `<release>-<server>`) in `secrets.externalSecret.storeName`.
- `secrets.existingSecret`, `secrets.existingSecretKey` (default: empty, `api-key`): Use an existing Secret instead.

## How to login

No human login: the gateway authenticates to the vendor with the team's API key, and agents authenticate to the gateway with their own identity.

## Delivery instructions

1. Create an API key in the vendor's account, on a plan that includes MCP or API access.
2. Put it into `secrets.apiKey`, or into a vault item for `secrets.type: externalSecret`.
3. Install the chart into the project of the agents.
4. Give the agents the server at `/mcp/<namespace>/<server>` on the gateway.

Every agent of the project spends the vendor's API units with the same key: keep an eye on the vendor's usage page. Results come from the open web: an agent that reads them can be steered by their content, so review what it does.
