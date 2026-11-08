# hermes

## Purpose

Runs one Hermes Agent (Nous Research) for a person or a team, with its web dashboard behind an OpenShift login. The dashboard is the chat, the settings and the MCP server list; it listens on 127.0.0.1 only, so the OpenShift `oauth-proxy` in the same pod is the only way in.

One pod holds:

- `gateway`: the Hermes gateway (cron jobs, background agents), run without its s6 supervisor and restarted by the kubelet.
- `dashboard`: the web dashboard on loopback.
- `oauth-proxy`: the OpenShift login in front of the dashboard; the only port in a Service.
- `token-proxy` (with `gateway.upstream`): on 127.0.0.1, adds the pod's ServiceAccount token (audience: the host of `gateway.upstream`) to every model and MCP call to the gateway, so `config.yaml` holds no key.
- `mcp-sync` (with `mcp.enabled`): turns every kagent `RemoteMCPServer` the ServiceAccount may list into an entry of `mcp_servers` in `config.yaml`, every `mcp.interval` seconds.

With `sso.enabled` (the default) the dashboard also runs its own OpenID Connect login (Hermes dashboard auth provider `self-hosted`) after the OpenShift login, while the Secret `sso.secretName` (default `public-<release>`) exists and `route.host` is set. The chat opens its WebSockets only from the browser origin Hermes knows as its public address, and Hermes serves a public address only behind a login of its own: without that login the dashboard loads behind a Route but every chat socket is closed. The chart sets that address to `https://<route.host>` and reads the issuer, client ID and client secret from the Secret. The client is confidential when the Secret holds a client secret, else public (PKCE); either way it needs the redirect URI `https://<route.host>/auth/callback`. `oauth-proxy` stays the group check; Hermes admits every user the identity provider signs in.

The Secret is an optional volume of the `dashboard` container, and its start script `dashboard.py` reads it every `sso.interval` seconds. Without the Secret the dashboard runs without a login of its own. When the Secret appears, changes or goes, the script restarts the dashboard process in the same container with the new login or without one; the pod keeps running, and the dashboard is not ready for a few seconds. A platform that makes the client and its Secret from a label on the workload gets that label through `statefulSet.labels`.

The Hermes home (`HERMES_HOME`, `/opt/data/home`) persists on the data volume: `config.yaml`, sessions, memories, skills the agent writes and MCP OAuth tokens. The pod runs as the image's own user (`image.uid`, 10000): Hermes refuses a home its user does not own. The init container `seed-config` creates the home with mode `0700`, merges the chart's keys into `config.yaml` (the model block, the skill directory, `config`) and keeps every other key, so settings made in the dashboard survive a restart. The agent containers get no ServiceAccount token; only `oauth-proxy` and `mcp-sync` do.

With `gateway.upstream` the model is `provider: custom` at the token proxy, default model `llm.model`. Hermes needs a model with a context window of at least 64000 tokens.

`mcp-sync` writes an entry per `RemoteMCPServer`: a server on `gateway.upstream` is called through the token proxy; a server annotated `openhands.dev/auth: oauth2` is one each user authorizes in the dashboard (**MCP > Authorize**), with `oauth.redirect_uri` `https://<route.host>/api/mcp/oauth/callback/<name>`, the pre-registered client of the Secret named by `openhands.dev/oauth-client` (keys `client_id`, `client_secret`, in the namespace of the `RemoteMCPServer`) and the scopes of `openhands.dev/oauth-scopes`; without a client annotation Hermes registers itself (dynamic client registration). The vendor must accept that callback as a redirect URI. A server that needs headers from a Secret and is not on the gateway is skipped. It changes and removes only the entries it wrote; an entry the user adds in the dashboard stays, and wins over a discovered server of the same name. A `RemoteMCPServer` labelled `<mcp.labelDomain>/addon: "true"` is used only when its bundle is in `mcp.bundles` or its server name is in `mcp.servers`. A server named in `mcp.exclude` (`<name>` or `<namespace>/<name>`) is never used.

`skills.repositories` are cloned at every start into `/opt/data/skills-external`, which `config.yaml` lists in `skills.external_dirs`; every directory with a `SKILL.md` under `path` is a skill. A failed clone keeps the skills of the last start.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install assistant grncloud/hermes \
  --set route.host=hermes.apps.example.com \
  --set oauth.ownerEmail=me@example.com \
  --set gateway.upstream=https://api.example.com \
  --set llm.model=my-model
```

The Secret `public-assistant` holds `sso-issuer-url`, `sso-client-id` and `sso-client-secret` of a client with redirect URI `https://hermes.apps.example.com/auth/callback`.

- `team`, `tenant` (default: empty): with `team`, every object goes into `<tenant>-<team>`. Empty: the release namespace.
- `image.repository`, `image.tag`, `image.digest` (default: `docker.io/nousresearch/hermes-agent`, `v0.23.0`, its digest): the upstream image.
- `image.uid` (default: 10000): the image's user; the pod runs as it.
- `route.host` (default: empty): hostname of the dashboard; also the base of the MCP OAuth callback. Empty: OpenShift assigns one and OAuth servers get no callback.
- `route.annotations` (default: cert-manager `ClusterIssuer` `letsencrypt`): used when a host is set.
- `sso.enabled` (default: true): Hermes' own login, required for the chat behind a Route; needs `route.host`. Without the Secret it does nothing.
- `sso.secretName` (default: empty, `public-<release>`): Secret in the release namespace with the OpenID Connect client.
- `sso.keys.issuer`, `sso.keys.clientId`, `sso.keys.clientSecret` (default: `sso-issuer-url`, `sso-client-id`, `sso-client-secret`): its keys; without the client secret key the client is public.
- `sso.scopes` (default: `openid profile email`).
- `sso.interval` (default: 30): seconds between two reads of the Secret.
- `statefulSet.labels` (default: empty): extra labels on the StatefulSet only, for a platform policy that creates the client and its Secret from a label.
- `oauth.ownerEmail` (default: empty): OpenShift user name of the only person admitted. Empty: `oauth.allowedGroups`; both empty: everyone who may edit the project.
- `oauth.allowedGroups`, `oauth.loginClusterRole` (default: empty): groups admitted, through a ClusterRole with verb `login` on `services/proxy` or the chart's own Role.
- `secrets.type` (default: `kubernetes`): `externalSecret` reads the cookie secret from `secrets.externalSecret.items.cookieSecret` of `secrets.externalSecret.storeName`.
- `secrets.cookieSecret` (default: empty): oauth-proxy cookie secret; empty: generated once. Under Argo CD pass it, or it changes on every sync.
- `gateway.upstream`, `gateway.audience` (default: empty): model and MCP gateway, audience of the token.
- `llm.model`, `llm.contextLength` (default: empty, 0): default model and its context window.
- `llm.providerName` (default: empty): name of the gateway in `/model` and `hermes status`, for example `<tenant>-bifrost` for a tenant's own gateway. Empty: Hermes shows "Custom endpoint".
- `a2a.enabled` (default: false): A2A v1.0 on Service `<release>-a2a`, port `a2a.port` (8090). The gateway's A2A server stays on loopback; sidecar `a2a-auth` admits a bearer JWT of the tenant: a ServiceAccount token from a namespace `<tenant>` or `<tenant>-*` (`a2a.tenantAuth.kubernetesIssuer`), or an OIDC token whose groups claim holds `<tenant>` or `<tenant>-*` (`a2a.tenantAuth.oidcIssuers`). No credential is stored. A caller of A2A v0.3 (kagent) gets the answers in v0.3 shape. With kagent installed, `a2a.kagent.enabled` registers a BYO Agent named like the release whose relay pod forwards the caller's token.
- `mcp.*`: discovery, see Purpose. `mcp.clusterRole` (default: `kagent-remotemcpserver-view`) is bound in the namespace so `mcp-sync` may list `RemoteMCPServers`.
- `skills.repositories` (default: empty): `{name, url, ref, path}` per repository.
- `config` (default: empty): keys merged into `config.yaml` at every start; they win over the dashboard.
- `extraVolumes`, `extraVolumeMounts` (default: empty): extra volumes, mounted in `gateway` and `dashboard`.
- `storage.size`, `storage.className`, `storage.accessMode` (default: `10Gi`, default class, `ReadWriteOnce`).
- `resources`, `dashboard.resources`, `oauthProxy.resources`, `tokenProxy.resources`, `mcpSync.resources`: per container.

## How to login

Open `https://<route.host>/` and log in with OpenShift. The owner (`oauth.ownerEmail`) or a member of `oauth.allowedGroups` is admitted; everyone else gets 403. With the single sign-on Secret Hermes then signs you in at its identity provider; with a single sign-on session from the OpenShift login this needs no second password. MCP servers that need your own account show under **MCP**: choose **Authorize** and log in at the vendor.

## Delivery instructions

- The address of the dashboard: `oc get route <release> -o jsonpath='https://{.spec.host}/{"\n"}'`.
- Whether Hermes' own login is on: `oc logs statefulset/<release> -c dashboard | grep 'dashboard: single sign-on'` (`on` names the issuer, client and public address; `off` names the reason). The Secret: `oc get secret public-<release>`.
- `oc logs statefulset/<release> -c mcp-sync` lists every MCP server added, changed, removed or skipped, with the reason.
- `oc exec statefulset/<release> -c gateway -- /opt/hermes/.venv/bin/hermes chat -q "hello"` asks the configured model one question from the pod.
