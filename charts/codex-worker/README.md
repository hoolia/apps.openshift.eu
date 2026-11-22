# Codex worker

## Purpose

Run one private HTTPS Codex review worker in the release namespace. The worker serializes
accepted reviews and keeps authentication state on persistent storage. It exposes
`POST /reviews` and unauthenticated `GET /metrics` on the internal Service at port 3443.
No public Route, DNS record, cluster resource or authentication bootstrap is created.

## Install

This chart does not meet the repository's working-with-no-values rule:
an operator must supply a real worker image and authentication configuration. Rendering
without them fails. No placeholder image or generated authentication credential is used.
The explicit `ci/test-values.yaml` fixture is for lint/render tests only.

Supply `image.repository`, `image.tag`, `gitlab.apiV4Url`, `gitlab.projectId`,
`authStore.projectId`, `gateway.url` and the three existing credential Secret names. The
worker and auth store use the same GitLab API host. Each Secret has key `token`; the
auth-store token belongs to the dedicated store project.

All model traffic goes through a model gateway. `gateway.url` is the HTTPS URL of the
gateway route that forwards to the Codex backend. The CLI sends the gateway key in header
`x-bf-vk` and the subscription token in `Authorization`; the gateway checks the key and
forwards the token. The worker starts no review without both. `image.pullSecrets` accepts existing registry Secret names
as a list of `{name: ...}` objects.

Select an ext4 block `storage.storageClass` with ReadWriteOncePod support, or supply
`storage.existingClaim`. The default new claim is 1Gi and ReadWriteOncePod; ReadWriteOnce
is available for a caller that accepts its weaker attachment constraint. Shared filesystems
and ReadWriteMany are unsupported. The chart cannot set or verify a storage class's
filesystem type. The admitted UID/group must be able to write the mounted parent directory.
OpenShift restricted-v2 supplies the namespace UID/group; the chart fixes neither.

Set `networkPolicy.runnerNamespaceSelector` and `networkPolicy.egress.dnsNamespaceSelector`
to nonempty namespace label selectors. Configure `dnsPorts` for the cluster's DNS service
and backend ports; OpenShift installations commonly need both 53 and 5353. Both TCP and UDP
are allowed per selected port. HTTPS egress permits TCP 443 to `httpsCidrs`, including the
GitLab host and Codex service. NetworkPolicy filters addresses and ports, not domain names.
Policies are additive: other tenant policies may also admit callers or egress.

```sh
helm install codex-worker ./charts/codex-worker \
  --namespace review --values worker-values.yaml
```

Every value and its default is described in `values.yaml`. The image is named by a version tag; `latest` is refused.
The chart and worker application version are 0.1.0; the image owns its Codex CLI version.

OpenShift TLS requests `<release>-tls` with `tls.crt` and `tls.key`. To use an existing
certificate on another Kubernetes distribution, set `tls.openshift.enabled=false`,
`tls.openshift.injectServiceCa=false`, and `tls.existingSecret`. The certificate must
cover `<release>.<namespace>.svc`. Callers must trust its issuer and send the matching
server name; Node.js callers may load their trust bundle through `NODE_EXTRA_CA_CERTS`.

For chart-managed ExternalSecrets, set `secrets.type=externalSecret`, the existing store
name/kind and explicit `items.workerToken`, `items.authStoreToken` and `items.gatewayKey`
references. These render one ExternalSecret per credential, each targeting key `token`,
with names `<release>-worker-token`, `<release>-auth-store-token` and
`<release>-gateway-key`. An existing Secret name overrides
its own ExternalSecret independently. Remote item keys are passed unchanged to the store.
No store or namespace access exception is created.

## How to login

There is no web login, admin user or single sign-on configuration. The runner sends
`Authorization: Bearer <token>` using key `token` of `secrets.workerTokenSecret`, or of the
chart-managed `<release>-worker-token`. The worker reads its separate auth-store token
from `secrets.authStoreTokenSecret` or `<release>-auth-store-token`, also key `token`.
Bearer authentication is the review request boundary even when other policies admit traffic.

The operator must provision usable Codex subscription authentication in the dedicated
store before submitting reviews. The chart never fabricates that authentication.

## Delivery instructions

The Service name is the release name. It listens on HTTPS 3443. The Deployment has one
replica, Recreate strategy, no Kubernetes API token and no fixed UID. It drops all
capabilities and uses a read-only root filesystem.

The pod runs two containers from the same image. Container `worker` serves the requests
and holds the request token, the TLS key, the auth-store token, the gateway key and the
private state. Container `codex` runs the Codex CLI: it has no Secret, no token variable
and no mount of the private state, and it listens on loopback only. The two share the pod
network and one directory, `/var/lib/codex/exchange`, through which the worker hands the
session and the gateway key of one review to the CLI and takes the session back.

The claim is mounted by `subPath`: `state` in `worker` only, `exchange` in both. An init
container creates both directories with mode 0700 under the admitted UID. Each container
has its own `/tmp`.

TCP readiness proves that the service process is listening. It does not prove inference
readiness or usable subscription authentication. No liveness probe restarts an auth failure.

Monitoring is off by default. With `monitoring.enabled=true`, install the ServiceMonitor
CRD first and supply `networkPolicy.monitoringNamespaceSelector` when policy is enabled.
Scrapes use HTTPS `/metrics` on the same 3443 port without bearer authentication.
`tls.openshift.injectServiceCa` creates a dedicated, empty `<release>-service-ca`
ConfigMap. Service CA injection replaces its data; application settings never share it.
Without injection, supply `tls.caConfigMap.name` and its bundle key in the release
namespace. The ServiceMonitor verifies `<release>.<namespace>.svc`; TLS verification
is never disabled.

The metrics are `codex_worker_reviews_total{result="success"|"failure"}`,
`codex_worker_pending_reviews` and `codex_worker_oldest_review_age_seconds`.
`monitoring.rules.enabled` adds tenant-local failure, stall and unavailable alerts scoped
to this namespace and Service. It requires monitoring and a `runbookUrl` base; lowercase
alert names become anchors. These alerts do not prove inference availability while idle.
The operator owns the runbook, scrape discovery and tenant monitoring enablement.

Before upgrade, rotation or removal, stop submissions and allow queued reviews to drain.
SIGTERM stops acceptance and drains accepted work within `terminationGracePeriodSeconds`.
Choose a budget that covers the queued work; do not force-delete the pod. Tokens and TLS
files are read at process start, so complete a controlled drain and restart after their
contents rotate. Chart-managed ExternalSecret reference changes update the pod checksum;
provider-side credential changes still need that controlled restart.

New claims carry both Helm retention and Argo prune/delete protection. Removal retains
the state claim; back it up securely and explicitly reuse `storage.existingClaim` during
reinstallation. Restore the saved filesystem into an ext4 block claim while no worker owns
it, preserve the original UID and private directory permissions, then attach one worker.
A new namespace UID may require an operator-managed ownership migration before startup.
Credential and TLS Secrets remain separately managed by their owners. Never run a second
worker against the same state or remove state during an active review.

Source checks require Helm, Python with PyYAML and, for alert tests, promtool:

```sh
helm lint charts/codex-worker -f charts/codex-worker/ci/test-values.yaml
python3 -m unittest discover -s charts/codex-worker/ci -p 'test_*.py'
```
