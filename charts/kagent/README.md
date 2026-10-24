# kagent

## Purpose

Runs the kagent agent controller and its web UI for one tenant in your project, behind the OpenShift login. Agents (`Agent` objects) in your project, and in the team projects the platform assigns to this controller, are deployed and served by it; the UI lists and chats with them.

The chart wraps the upstream [kagent chart](https://github.com/kagent-dev/kagent) (`upstream.*` values) and keeps everything inside the release namespace:

- `kagent-login` (OpenShift oauth-proxy) logs people in through the cluster OAuth server with its ServiceAccount as OAuth client and forwards to the UI. The Route points at it.
- The UI and controller come from upstream; RBAC is namespaced (`upstream.rbac.namespaces`), never cluster-wide.
- NetworkPolicies admit the login proxy from the router only and the UI from the login proxy only, so the upstream UI Route answers nothing.
- The controller runs in auth mode `unsecure`: the login proxy authenticates, and every user of one instance acts as the same kagent user (`admin@kagent.dev`), so all of them see the same chat sessions. Per-user sessions need a JWT from an OIDC provider (upstream `oauth2-proxy` and `controller.auth.mode: trusted-proxy`), which the OpenShift login does not issue.

Choices:

- The CRDs, the kmcp controller access, the tool servers of team namespaces and the controller's access to them belong to the platform (kagent-operator), not to this chart: kmcp, kagent-tools and the built-in agents are off.
- The bundled PostgreSQL runs under the project UID. It is upstream's development database; point `upstream.database.postgres.url` or `urlFile` at your own PostgreSQL for production.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install kagent grncloud/kagent -n team-kagent \
  --set 'upstream.rbac.namespaces={team-kagent}' \
  --set route.host=kagent.apps.example.com
```

With Argo CD, set the namespace from the destination: `spec.source.helm.parameters` `upstream.rbac.namespaces[0]` = `$ARGOCD_APP_NAMESPACE`.

- `upstream.rbac.namespaces` (required): The release namespace. The chart refuses to render without it, because empty gives cluster-wide RBAC.
- `route.host` (default: empty): Hostname. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `route.labels`, `route.timeout` (default: empty, 3600s), `route.annotations` (default: cert-manager issuer, used when `route.host` is set).
- `login.allowedGroups` (default: empty): OpenShift groups admitted. Only their members log in: the chart grants them the verb `login` on `services/proxy` of the login Service, which `admin` and `edit` do not hold. Empty: everyone who may edit the project.
- `login.loginClusterRole` (default: empty): ClusterRole holding the verb `login` on `services/proxy`; `login.allowedGroups` are bound to it instead of to the chart's own Role. An installer without that permission (a project admin, the project's Argo CD) needs this value.
- `login.image` (default: `openshift/oauth-proxy:v4.4` from the cluster registry), `login.port` (default: 4180), `login.resources`.
- `secrets.cookieSecret` (default: empty): Login cookie key, generated once and kept on upgrade.
- `networkPolicy.enabled` (default: true), `networkPolicy.agentNamespaceSelector` (default: empty): Namespaces whose agent pods reach the controller API.
- `upstream.providers` (default: upstream OpenAI with Secret `kagent-openai`): The default model. Create the API key Secret, or point `upstream.providers.openAI.config.baseUrl` at an OpenAI-compatible gateway.
- `upstream.controller.env` (default: empty): Extra controller environment, for example `WATCH_NAMESPACES` from a pod annotation the platform writes.

## Delivery instructions

- People log in through the cluster's OAuth server with their OpenShift account; no login client is needed.
- Team projects are served when the platform assigns them: it writes the controller's watch list and binds the controller in each of them. Without that, the controller serves the release namespace only.
- The default `ModelConfig` reconciles once its API key Secret exists.
- The upstream chart also renders a Route `kagent-ui` straight to the UI; the NetworkPolicies keep it without answer. Do not add a policy that admits the router to the UI.
