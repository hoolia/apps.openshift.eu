# mcp-odoo

Odoo as an MCP server ([ivnvxd/mcp-server-odoo](https://github.com/ivnvxd/mcp-server-odoo)),
streamable HTTP on port 8000, path `/mcp`, for a tenant or one of its teams. Agents search,
read, create and update Odoo records: contacts, leads, sales orders, mailings, and any other
model the Odoo user may reach.

## Purpose

- One Odoo user for every caller: the server acts as `odoo.user` with the API key in
  `credentials.existingSecret` (`ODOO_API_KEY`, or `ODOO_PASSWORD`). A person makes the key
  in that user's Odoo preferences (Account Security, New API Key). What the agents may do is
  what that user may do, so give it only the groups the agents need.
- `odoo.yolo` decides which models the server offers:
  - `off` (default): only the models enabled in the Odoo MCP module (`mcp_server`, from the
    same project), which must be installed in Odoo.
  - `read`: every model the user may read, read-only, without the module.
  - `true`: every model the user may read and write, with create, update and delete, without
    the module.
- Odoo 19 and later with an API key talk JSON-2; older versions XML-RPC, where `odoo.user` is
  required for `read` and `true`.
- `odoo.allowedCompanies` limits every call to the listed companies, `odoo.locale` sets the
  language of Odoo's answers, `odoo.sessionIdleTimeout` closes idle sessions (default 600 s).
- The Service follows the gateway's MCP discovery conventions: port `appProtocol`
  `agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and
  `agentgateway.dev/mcp-target-name` (tools `<targetName>_<tool>`). Service and Deployment
  carry the catalog labels `mcp.openshift.eu/server: odoo` and
  `mcp.openshift.eu/category: crm`.
- With `team`, every object goes into `<tenant>-<team>`, a namespace the team already has;
  without it, into the release namespace (for example the tenant's home namespace).
- Egress reaches the public internet only, plus DNS. No cluster-scoped objects, no Kubernetes
  API access.

### Token check

The server itself asks callers for no credential. Behind an MCP gateway that authenticates
callers, `networkPolicy.ingressNamespaces` admits only the gateway. Without such a gateway,
`auth.enabled` puts the token check of the library chart `mcp-auth` in front of the server: an
oauth2-proxy sidecar takes the Service port and the server listens on
`127.0.0.1:auth.upstreamPort` (default: 18000) only. Every request needs a bearer JWT that
`auth.issuerUrl` signed for one of `auth.audiences` (default: the release namespace), else
401; a token whose groups claim names none of `auth.allowedGroups` gets 403. The README of
`mcp-auth` lists every `auth.*` value.

## Install

```bash
oc create secret generic odoo-mcp --from-literal=ODOO_API_KEY=<api key>
helm repo add apps https://apps.openshift.eu
helm install odoo apps/mcp-odoo \
  --set odoo.url=https://erp.example.com \
  --set odoo.db=<database> \
  --set odoo.user=<login> \
  --set credentials.existingSecret=odoo-mcp
```

For a team of a tenant, add `--set team=<team> --set tenant=<tenant>`.

## How to login

No human login: the server acts as one Odoo user through its API key; callers authenticate to
the MCP gateway in front of it, or with a bearer token when `auth.enabled` is set.

## Delivery instructions

- Ready when the Deployment `<release>` is Available and its log says
  `Successfully connected to Odoo`. When Odoo cannot be reached the server still starts, logs
  `Odoo is unreachable; serving anyway` and retries on the next call; a tool call then
  returns the cause.
- The Service `<release>` answers on port 8000, path `/mcp`: `initialize`, then `tools/list`
  returns tools such as `search_records`, `get_record` and, with `odoo.yolo: "true"`,
  `create_record` and `update_record`.

## Values

- `team` (default: empty), `tenant` (default: empty): team namespace `<tenant>-<team>`.
- `image.repository`, `image.tag` (default: `ghcr.io/ivnvxd/mcp-server-odoo`, `0.9.0`).
- `odoo.url` (default: empty): address of the Odoo instance; required.
- `odoo.db` (default: empty): database; empty: detected, which needs database listing.
- `odoo.user` (default: empty): login of the Odoo user.
- `odoo.yolo` (default: `off`): `off`, `read` or `true`.
- `odoo.allowedCompanies`, `odoo.locale` (default: empty).
- `odoo.sessionIdleTimeout` (default: `600`): seconds; empty: sessions never expire.
- `credentials.existingSecret` (default: empty): Secret with `ODOO_API_KEY` or
  `ODOO_PASSWORD`; required.
- `env` (default: `{}`): extra environment variables of the server, for example
  `ODOO_MCP_DEFAULT_LIMIT`.
- `port`, `path` (default: `8000`, `/mcp`).
- `mcp.appProtocol` (default: `agentgateway.dev/mcp`), `mcp.targetName` (default: `odoo`),
  `mcp.server` (default: `odoo`), `mcp.category` (default: `crm`), `mcp.bundle` (default:
  empty).
- `service.labels`, `service.annotations` (default: `{}`).
- `podLabels`, `podAnnotations` (default: `{}`).
- `resources` (default: 10m/96Mi request, 1 CPU/512Mi limit).
- `auth.*` (default: off): the token check, see above.
- `networkPolicy.enabled` (default: true), `networkPolicy.ingressNamespaces` (default: `[]`):
  namespaces that may call the server besides its own, for example the gateway's.
- `networkPolicy.egress.*`: internet egress except private ranges, plus DNS.
