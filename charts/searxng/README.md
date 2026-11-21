# searxng

## Purpose

SearXNG: a private metasearch engine that asks Google, Bing, DuckDuckGo and many more for you and keeps no profile of who searched. The chart runs the upstream SearXNG image for one team, and next to it [mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng): web search tools for AI agents (`searxng_web_search`, `web_url_read`) on streamable HTTP.

- The tool server's Service carries `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path` and `agentgateway.dev/mcp-target-name`, so an MCP gateway that discovers Services publishes the tools to the project's agents as `<targetName>_<tool>`.
- By default only the tool server reaches SearXNG; `route.enabled` adds the web interface for people.
- SearXNG keeps no data: no volume.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install searxng grncloud/searxng
```

- `settings` (default: limiter, image proxy and metrics off, formats html and json): `settings.yml` on top of the SearXNG defaults; `server.secret_key` comes from the Secret.
- `resources` (default: requests 50m CPU and 256Mi, limits 1 CPU and 1Gi), `podLabels`.
- `secrets.type` (default: `kubernetes`): `externalSecret` reads the secret key from the password of vault item `secrets.externalSecret.item` (default `<release>-secret`) in `secrets.externalSecret.storeName`.
- `secrets.secretKey` (default: empty): generated at the first install and kept on upgrade. `secrets.existingSecret`: a Secret with the key `SEARXNG_SECRET`.
- `route.enabled` (default: false), `route.host`, `route.labels`, `route.annotations`: the web interface; put the cert-manager issuer annotations in `route.annotations`.
- `mcp.enabled` (default: true): the tool server.
- `mcp.targetName` (default: the release name): tool prefix on the gateway.
- `mcp.allowedHosts` (default: empty): further Host header values the tool server answers, for example the gateway's public host name; its Service names are always allowed.
- `mcp.ingressNamespaces` (default: empty): label selectors of the namespaces that may call the tools besides the own one, for example the gateway's.
- `mcp.serviceLabels`, `mcp.podLabels`, `mcp.extraEnv`, `mcp.resources`: further settings of the tool server.
- `mcp.extraEgressExceptCidrs` (default: empty): your own public ranges, which `web_url_read` must not reach.

A tool that renders the chart without access to the cluster, such as Argo CD, generates a new secret key on every render: set `secrets.secretKey` there, or use `secrets.type=externalSecret`.

## How to login

- **Single sign-on**: none. The web interface (`route.enabled`) has no login; anyone who reaches it can search.
- **Admin user**: none; SearXNG has no accounts.

## Delivery instructions

- Check the pods are Ready: `oc get pods -l app.kubernetes.io/instance=<release>`.
- From a pod in the project, `curl 'http://<release>:8888/search?q=test&format=json'` returns results.
- Through the gateway the tools appear as `<targetName>_searxng_web_search` and `<targetName>_web_url_read`.
- Search engines rate-limit busy addresses: when results stay empty, read the SearXNG log for the engines that refused, and switch them off in `settings.engines`.
- The tool server asks callers for no login: keep `mcp.ingressNamespaces` to the gateway, and let the gateway authenticate callers.
