# mcp-sonarqube

SonarQube as an MCP server
([SonarSource sonarqube-mcp-server](https://github.com/SonarSource/sonarqube-mcp-server)),
streamable HTTP on port 8000, path `/mcp`, for a tenant or one of its teams.

## Purpose

- The server holds no SonarQube account. Every request carries a SonarQube **user** token in
  `Authorization: Bearer <token>` and runs as that user; a request without one gets 401.
- `credentials.mode: perCaller` (default): each caller brings its own user token. Behind an
  MCP gateway the caller's `Authorization` authenticates it to the gateway; the Service
  annotation `agentgateway.dev/mcp-credential-header` (from `credentials.gatewayHeader`,
  default `X-SonarQube-Authorization`) names the header the gateway copies into the
  backend's `Authorization`.
- `credentials.mode: broker`: a token broker in the same namespace gives every agent its own
  SonarQube user without any setup per agent. The gateway sends it each caller's
  `Authorization` (Service annotation `agentgateway.dev/mcp-credential-extauth`). The broker
  verifies the JWT against `broker.issuers`, maps it to the login
  `<serviceaccount>.<namespace>.<cluster>` (a ServiceAccount token), or to the client id of
  an OpenID Connect agent client (a client-credentials token whose `preferred_username` is
  that client id or `service-account-<client id>`), creates that
  non-local user on first use, mints a user token for it and answers with
  `Authorization: Bearer <token>`. It never mints for a local or single-sign-on account, and
  people are not served. The broker holds the token of a SonarQube administrator.
- `broker.identityProvider` decides whose account the broker creates:

  | Value | SonarQube account | Groups |
  |---|---|---|
  | `sonarqube` (default) | non-local user, provider `sonarqube` | `broker.groups`, at creation |
  | `oidc` | user of plugin [sonar-auth-oidc](https://github.com/vaulttec/sonar-auth-oidc), provider `broker.oidc.providerKey`: external login `preferred_username`, external id `sub`, the account a browser login of the same identity finds (login strategy "Preferred username") | `broker.groups` at creation, plus every group of the token's `broker.oidc.groupsClaim` named `broker.oidc.groupPrefix*`, kept in step on each request |

  With `oidc`, people and agents are authorized the same way: through SonarQube groups named
  after the identity provider's groups. The broker creates a missing `<groupPrefix>*` group
  and removes the user from `<groupPrefix>*` groups the token no longer carries; other groups
  stay as they are. A failed sync answers 502. An empty `groupPrefix` syncs no group.
  SonarQube must have sonar-auth-oidc installed and enabled, with groups sync on the same
  claim. A broker user of provider `sonarqube` moves to `oidc` on its next token.
- SonarQube's Web API accepts SonarQube user tokens only, not OpenID Connect tokens, so both
  modes hand the MCP server a SonarQube user token.
- `sonarqube.readOnly: true` (default) switches every write tool off; the user's SonarQube
  permissions (Browse through the default group) decide which projects it reads.
- The Service follows the gateway's MCP discovery conventions: port `appProtocol`
  `agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and
  `agentgateway.dev/mcp-target-name` (tools `<targetName>_<tool>`). Service and Deployment
  carry the catalogue labels `mcp.openshift.eu/server`, `mcp.openshift.eu/category` and, when
  set, `mcp.openshift.eu/bundle`.
- No cluster-scoped objects, no Kubernetes API access. The server reaches the public internet
  only, plus `networkPolicy.egress.allow`.

## Install

```bash
helm repo add apps https://apps.openshift.eu
helm install sonarqube apps/mcp-sonarqube --set sonarqube.url=https://sonarqube.example.org
```

With the token broker, create a Secret with an administrator's user token first and pass the
issuers your gateway accepts:

```bash
oc create secret generic sonarqube-admin --from-literal=token=<ADMIN_USER_TOKEN>
helm install sonarqube apps/mcp-sonarqube -f values-broker.yaml
```

```yaml
# values-broker.yaml
sonarqube:
  url: https://sonarqube.example.org
credentials:
  mode: broker
broker:
  adminTokenSecret: sonarqube-admin
  cluster: example
  issuers:
  - issuer: https://kubernetes.default.svc
    jwksUri: https://kubernetes.default.svc/openid/v1/jwks
    kind: kubernetes
    audiences: [mcp.example.org]
    caFile: /etc/kube-ca/ca.crt
```

With sonar-auth-oidc users and groups, add to `values-broker.yaml` an OpenID Connect issuer
(the one SonarQube's sonar-auth-oidc trusts) and the group prefix of your tenant:

```yaml
broker:
  identityProvider: oidc
  oidc:
    groupPrefix: example-
  issuers:
  - issuer: https://idp.example.org/realms/example
    jwksUri: https://idp.example.org/realms/example/protocol/openid-connect/certs
    kind: oidc
```

For a team of a tenant, add `--set team=<team> --set tenant=<tenant>`.

## Delivery instructions

- Ready when the Deployment `<release>` (and `<release>-broker` in broker mode) is Available
  and `GET /health` on port 8000 answers 200.
- perCaller: create a SonarQube user token per caller (My Account, Security) and send it in
  the header named by `credentials.gatewayHeader`.
- broker: an agent's first call creates the SonarQube user `<serviceaccount>.<namespace>.<cluster>`;
  grant it projects through a group (`broker.groups`, with `oidc` also the synced
  `<groupPrefix>*` groups) or the default group. Rotate the
  administrator token by replacing the Secret and restarting `<release>-broker`; the broker
  then mints new tokens and revokes its old ones.
- A call without a credential answers 401; that is expected for a caller without a token.

## Values

- `team`, `tenant` (default: empty): team namespace `<tenant>-<team>`.
- `image.repository`, `image.tag` (default: `docker.io/sonarsource/sonarqube-mcp`, `1.30.0.4763`).
- `sonarqube.url` (required), `sonarqube.readOnly` (default: true), `sonarqube.toolsets`
  (default: empty, the server's default set).
- `credentials.mode` (default: `perCaller`): `perCaller` or `broker`.
- `credentials.gatewayHeader` (default: `X-SonarQube-Authorization`).
- `broker.adminTokenSecret`, `broker.adminTokenKey` (default: empty, `token`): required in
  broker mode.
- `broker.issuers` (default: `[]`), `broker.cluster` (default: empty): required in broker mode.
- `broker.allowedIdentity` (default: empty): regular expression a login must match.
- `broker.groups` (default: `[]`), `broker.tokenDays` (default: 30), `broker.port` (default: 9090).
- `broker.identityProvider` (default: `sonarqube`): `sonarqube` or `oidc`.
- `broker.oidc.providerKey`, `broker.oidc.groupsClaim`, `broker.oidc.groupPrefix` (default:
  `oidc`, `groups`, empty): with `oidc` only.
- `port`, `path` (default: `8000`, `/mcp`).
- `mcp.appProtocol`, `mcp.targetName` (default: `agentgateway.dev/mcp`, `sonarqube`).
- `mcp.server`, `mcp.category`, `mcp.bundle` (default: `sonarqube`, `code-quality`, empty).
- `service.labels`, `service.annotations`, `podLabels`, `podAnnotations` (default: `{}`).
- `resources` (default: 50m/256Mi request, 1 CPU/768Mi limit).
- `networkPolicy.enabled` (default: true), `networkPolicy.ingressNamespaces` (default: `[]`),
  `networkPolicy.egress.*`.
