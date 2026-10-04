# OmniRoute

## Purpose

OmniRoute is a self-hosted AI proxy: one endpoint that routes LLM requests to the
providers you configure in its dashboard. The chart runs the upstream image with a Redis
instance and two volumes.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install omniroute grncloud/omniroute

Read the address and the first password:

    oc get route omniroute -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret omniroute-secret -o jsonpath='{.data.INITIAL_PASSWORD}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname of the dashboard. Empty: OpenShift assigns one. |
| `persistence.size` | `5Gi` | Size of the data volume. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the four secrets from a secret store. |
| `secrets.initialPassword` | empty | First dashboard password. Empty: generated. |
| `image.tag` | chart appVersion | Upstream image tag. |
| `build.enabled` | `false` | Build the image from source in your project instead. Takes about an hour and 8Gi of memory. |

Secrets left empty are generated at the first install and kept on every upgrade.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the record; the certificate follows
  automatically once it resolves.
- With `secrets.type=externalSecret`, create four items in your vault collection, named
  as in `secrets.externalSecret.items`, each with the value in the password field, and
  set `secrets.externalSecret.storeName` to your store.
