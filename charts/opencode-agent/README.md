# opencode-agent

## Purpose

Runs one personal OpenCode coding agent for one person in your project. The person works with it in the browser behind a login; other agents call it over A2A (agent-to-agent, port 8080). The agent works in a Git work tree on a persistent volume and acts on your project with its own ServiceAccount, which has the `edit` role in the project only.

The agent is a kagent `Agent` of type `BYO`: kagent renders the Deployment and the A2A Service from it. One pod holds four containers: the A2A adapter `opencode-a2a`, `opencode web` on 127.0.0.1, the login proxy `oauth2-proxy` in front of the UI, and a token proxy on 127.0.0.1 that adds the agent's credential to every model and A2A call to `gateway.upstream`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install sam grncloud/opencode-agent \
  --set person.name=sam --set person.email=sam@example.com \
  --set person.role="I am sales" \
  --set route.host=sam.apps.example.com \
  --set oauth.issuerUrl=https://login.example.com/realms/example \
  --set gateway.upstream=https://api.example.com
```

Objects are named `<person.name>-agent`. Read the address:

```bash
oc get route sam-agent -o jsonpath='https://{.spec.host}{"\n"}'
```

- `person.name` (default: the release name): Name prefix and Git user name.
- `person.email` (default: empty): Git email and the only email admitted to the UI. Empty: nobody logs in.
- `person.role` (default: empty): One line appended to the agent instructions.
- `route.host` (default: empty): Hostname of the UI. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `route.labels` (default: empty): Extra labels on the Route only.
- `route.timeout` (default: 3600s): Router timeout; the UI streams.
- `route.annotations` (default: cert-manager issuer): Used when `route.host` is set.
- `oauth.issuerUrl`, `oauth.clientId`: OIDC issuer and client of the UI login. Empty client: `<person.name>-agent`.
- `oauth.allowedGroups` (default: empty): The person must also be in one of these groups, read from the token claim `oauth.groupsClaim`.
- `secrets.clientSecret` (default: empty): Secret of the login client. Empty: a public client with PKCE.
- `secrets.serverPassword`, `secrets.a2aToken`, `secrets.cookieSecret` (default: empty): Generated once and kept on upgrade.
- `secrets.type` (default: kubernetes): `externalSecret` reads every secret from a secret store.
- `gateway.upstream` (default: empty): Model and A2A gateway. Required for a working agent.
- `gateway.audience` (default: the host of `gateway.upstream`): Audience of the agent's ServiceAccount token, mounted at `/var/run/secrets/api-token/token`.
- `a2a.gatewayNamespace` (default: empty): Namespace of the gateway that publishes this agent over A2A. Renders a ReferenceGrant for its HTTPRoutes and admits it in the NetworkPolicy.
- `a2a.allowedHosts`, `a2a.publicUrl`: Inbound Host allowlist and Agent Card address of the adapter.
- `git.url`, `git.branch`, `secrets.gitToken`: Repository cloned into `/workspace` on first start, never overwritten. The token goes to the host of `git.url` only.
- `opencode.plugins`, `opencode.npmScopes`, `opencode.env`, `opencode.config`: npm plugins, their registries, extra environment and overrides of `opencode.json`.
- `builds.enabled` (default: true): Build the OpenCode image (with git and kubernetes-mcp-server) and the adapter image in your project. When false, set `a2a.image`, and `opencode.image` to an image that carries git and `/tools/kubernetes-mcp-server`.
- `storage.size`, `storage.accessMode`, `storage.className` (default: 20Gi, ReadWriteMany, the default class): The work tree volume. It is kept when the release is removed.
- `serviceAccount.name` (default: empty): Empty: `<person.name>-agent`, created by kagent.
- `networkPolicy.a2aNamespaces` (default: empty): More namespaces allowed to call A2A on 8080.

OpenCode runs under an exclusive lock on the volume. A new pod starts while the old one still runs; its OpenCode waits for the lock and starts when the old pod stops.

## Delivery instructions

- kagent must run on the cluster: the chart renders a `kagent.dev/v1alpha2` `Agent`. To use the kagent UI, add kagent's namespace to `networkPolicy.a2aNamespaces`.
- The login needs an OIDC client at your issuer with the redirect address `https://<route.host>/oauth2/callback`. A public client uses PKCE; a confidential one needs `secrets.clientSecret`. Ask the platform team for a client at the platform's issuer.
- The A2A gateway publishes the agent to other agents. Ask the platform team for the gateway's namespace and set `a2a.gatewayNamespace`; the platform adds the route and copies the Secret `<person.name>-agent-a2a-auth` (key `token`) to the gateway.
- The gateway in `gateway.upstream` must accept ServiceAccount tokens of your project with the audience `gateway.audience`.
- `storage.accessMode` ReadWriteMany needs a StorageClass that offers it, such as a CephFS or NFS class. Set `storage.className` when it is not the default class. With ReadWriteOnce, a pod moved to another node waits until the old pod releases the volume.
- With `secrets.type=externalSecret`, set `secrets.externalSecret.storeName` and create one vault item per secret, named `<person.name>-agent-<server-password, a2a-auth, cookie-secret, git-token, oidc>` or as set in `secrets.externalSecret.items`, with the value in the password field. The cookie secret item must hold 16, 24 or 32 characters. Set `secrets.externalSecret.items.clientSecret` only for a confidential login client.
- Agents that share a project can read each other's Secrets and exec into each other's pods. Give an agent its own project when that is not acceptable.
