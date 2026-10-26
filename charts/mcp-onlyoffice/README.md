# mcp-onlyoffice

## Purpose

Runs the official ONLYOFFICE Docs MCP server [ONLYOFFICE/docs-mcp](https://github.com/ONLYOFFICE/docs-mcp) in your project for its agents, connected to your ONLYOFFICE Document Server. Agents create blank Word, Excel, PowerPoint and PDF documents, open existing documents from the file origins you allow (for example the Nextcloud that holds them), and save the edited result. Editing happens in the real ONLYOFFICE editor, embedded in the MCP client's answer: text, tables, drawings and shapes, comments and tracked changes, everything the editor offers.

| Tool | What it does |
|---|---|
| `create_file` | Creates a blank DOCX, XLSX, PPTX or PDF and opens it in the editor |
| `open_file` | Opens a document from a URL on an allowed origin, or a file the client uploads |
| `save_file` | Saves the open document and returns it as a resource link |

The editor is an MCP Apps widget (`ui://editor/index.html`): a client that renders MCP Apps shows it, a client that does not still gets the tool results but no editor. To read the text of a document without an editor, use a document parser such as the Nextcloud MCP server's `nc_webdav_read_file`.

The server signs every editor configuration with the Document Server's JWT secret, so it needs that secret. It listens on streamable HTTP at `/mcp` on port `port` and asks callers for no credential. Its Service carries the port `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name`, so an MCP gateway in front of it authenticates the callers and publishes the server; the NetworkPolicy admits the own project and `networkPolicy.ingressNamespaces` only. The Deployment and Service carry `mcp.openshift.eu/server: onlyoffice`, `mcp.openshift.eu/bundle: onlyoffice` and `mcp.openshift.eu/category: documents`.

Editor sessions live in the memory of the one server process: the chart runs one replica, and a restart ends every open editor session.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install onlyoffice grncloud/mcp-onlyoffice \
  --set image.repository=<registry>/docs-mcp --set image.tag=<tag> \
  --set documentServer.url=https://<document server> \
  --set secrets.jwtSecret=<JWT secret of the Document Server> \
  --set fileUrlAllowedOrigins[0]=<host of your Nextcloud>
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects and of the server on the gateway (tools `<name>_*`); empty: `mcp.server`.
- `image.repository`, `image.tag` (required): Server image. ONLYOFFICE publishes none; build it from the repository's `Dockerfile` (Delivery instructions).
- `documentServer.url` (required): Public URL of the ONLYOFFICE Document Server. The browser that shows the editor loads it as well.
- `documentServer.existingSecret` (default: empty): Existing Secret with the JWT secret under `documentServer.jwtSecretKey` (default `jwt-secret`); empty: `<release>-<documentServer.secretSuffix>` (default `onlyoffice-jwt`), written from `secrets`.
- `documentServer.jwtAlgorithm` (default: `HS256`), `documentServer.jwtExpiresIn` (default: 60 seconds): Signing of the editor configuration.
- `documentServer.outputFileMaxBytes` (default: 10485760): Largest saved document the server returns.
- `fileUrlAllowedOrigins` (default: empty): Origins, hosts or wildcard domains (`*.example.com`) `open_file` may open documents from. Empty: only uploaded files and new documents.
- `http.publicUrl` (default: empty): Public URL of the server, used as the editor widget's domain; its host then becomes the only accepted `Host` header besides `http.allowedHosts`. Leave both empty behind an MCP gateway.
- `http.corsAllowedOrigins` (default: empty): Browser origins allowed by CORS.
- `http.trustProxy` (default: `1`): Proxy hops in front of the server.
- `http.rateLimitMaxRequests` (default: 120): Requests per client per minute.
- `secrets.type` (default: `kubernetes`): `kubernetes` writes the Secret from `secrets.jwtSecret`; `externalSecret` reads `secrets.externalSecret.property` (default `login.password`) of the vault item `secrets.externalSecret.item` in `secrets.externalSecret.storeName`.
- `port` (default: 13001): Container and Service port.
- `mcp.appProtocol` (default: `agentgateway.dev/mcp`): Service port appProtocol for gateway discovery.
- `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `mcp.openshift.eu`, `onlyoffice`, `onlyoffice`, `documents`): Catalog labels.
- `service.labels`, `service.annotations` (default: the OpenHands icon annotation): Extra labels and annotations of the Service, for example the gateway's exposure label.
- `resources`: Requests and limits of the server.
- `networkPolicy.ingressNamespaces` (default: empty): Namespace selectors that may call the server besides its own project; the gateway's namespace belongs here. Egress goes to DNS and public addresses on port 443 only, which covers a Document Server and file origins reached by their public names.

The server reads the JWT secret once at start: after a new secret, `oc rollout restart deployment/onlyoffice`.

## Delivery instructions

1. Build the server image from the upstream repository, pinned to a commit, and push it to a registry your cluster pulls from. On OpenShift a BuildConfig does it:

   ```yaml
   source:
     type: Git
     git:
       uri: https://github.com/ONLYOFFICE/docs-mcp.git
       ref: <commit>
   strategy:
     type: Docker
     dockerStrategy: {}
   ```

2. Take the JWT secret of your Document Server: the `JWT_SECRET` of its container, which is also the secret your Nextcloud's ONLYOFFICE app signs with (`occ config:app:get onlyoffice jwt_secret`). Put it in `secrets.jwtSecret`, or in the password of a vault item for `secrets.type: externalSecret`.
3. Set `fileUrlAllowedOrigins` to the host of the Nextcloud (or other file store) whose documents agents open. The Document Server downloads the file from that URL itself, so the URL must be reachable from the Document Server without a login: a Nextcloud public share link, or a download URL the client passes.

Prompt injection: a document an agent opens can contain instructions. Keep agents that open documents from outside your organisation away from tools that write elsewhere.
