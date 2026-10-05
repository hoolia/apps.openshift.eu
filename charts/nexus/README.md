# Nexus Repository

## Purpose

Sonatype Nexus Repository: a private store and proxy for Maven, npm, PyPI, container
images and other package formats. The chart runs the upstream image with one data volume.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install nexus grncloud/nexus

Install one release per project. The first start takes a few minutes. Log in as `admin`
with:

    oc get route nexus -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret nexus-credentials -o jsonpath='{.data.admin-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one. |
| `persistence.size` | `20Gi` | Size of the data volume. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the password from a secret store. |
| `secrets.adminPassword` | empty | Admin password at the first start. Empty: generated. |
| `jvm.heap` | `1200m` | Java heap. Raise it together with `resources.limits.memory`. |

The password is applied at the first start only; change it in Nexus afterwards. It is
generated at the first install and kept on every upgrade with `helm upgrade`. A tool
that renders the chart without access to the cluster, such as Argo CD or
`helm template`, generates a new value on every render: set the password there.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- A container registry inside Nexus needs its own port and hostname, which this chart
  does not expose. Ask the platform team when you need one.
- With `secrets.type=externalSecret`, create the item named in
  `secrets.externalSecret.item` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
