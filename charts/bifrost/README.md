# Bifrost

## Purpose

Bifrost is a gateway for large language models: one OpenAI-compatible endpoint in front
of the providers you configure, with virtual keys, budgets and rate limits. The chart
runs the upstream Bifrost chart with a data volume, a login for the dashboard and a
Route.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install bifrost grncloud/bifrost

Install one release per project. Log in to the dashboard as `admin` with:

    oc get route bifrost -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret bifrost-credentials -o jsonpath='{.data.password}' | base64 -d

Add providers and virtual keys in the dashboard, or under `upstream.bifrost.providers`
and `upstream.bifrost.governance`; the values of the upstream chart are documented at
https://github.com/maximhq/bifrost.

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.adminPassword` | empty | Dashboard password. Empty: generated. |
| `upstream.storage.persistence.size` | `10Gi` | Size of the data volume. |

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, generates new values on every render: set both secrets
there. A changed encryption key makes stored provider keys unreadable.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection, each with the value in the
  password field, and set `secrets.externalSecret.storeName` to your store.
- Single sign-on in front of the gateway is a platform service and not part of this
  chart.
