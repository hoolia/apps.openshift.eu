# metabase

## Purpose

Metabase: questions, dashboards and reports on your own databases. The chart runs the
upstream Metabase OSS image with an application database of the CloudNativePG operator.
An OpenShift login in front of Metabase, limited to chosen groups, and a scheduled
database backup are options.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install metabase grncloud/metabase

Install one release per project. The first start takes a few minutes: the database is
created first and Metabase migrates it. Open the address and create the admin account in
the browser.

    oc get route metabase -o jsonpath='https://{.spec.host}{"\n"}'

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname. Empty: OpenShift assigns one. Also Metabase's site URL. |
| `oauthProxy.enabled` | `false` | OpenShift login before Metabase. |
| `oauthProxy.allowedGroups` | empty | Groups that pass the login. Empty: who may view this project's Services. |
| `oauthProxy.loginClusterRole` | empty | ClusterRole with verb `login` on `services/proxy`, bound to the groups; required with `allowedGroups`. |
| `database.instances` | `1` | PostgreSQL instances. `2` adds a standby. |
| `database.size` | `5Gi` | Size of the database volume of each instance. |
| `database.backup.enabled` | `false` | Daily backup through the barman-cloud plugin. |
| `database.backup.objectStoreName` | empty | barman-cloud `ObjectStore` in this project; required for a backup. |
| `secrets.existingSecret` | empty | Secret with the keys `MB_ENCRYPTION_SECRET_KEY` and `cookie-secret`. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `connectsTo` | empty | Data sources to draw in the console topology view. |
| `extraEnv` | empty | Further Metabase settings as environment variables. |

The key `MB_ENCRYPTION_SECRET_KEY` in the Secret `metabase` encrypts the stored
credentials of your data sources. Keep a copy of it: without the key Metabase cannot
read them. It is generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD, generates a new value on every render: set `secrets.encryptionKey` and
`secrets.cookieSecret`, or `secrets.existingSecret`.

`helm uninstall` deletes the application database with every question and dashboard.

## How to login

- **Single sign-on**: with `oauthProxy.enabled` the OpenShift login comes first; Metabase OSS then asks for its own account.
- **Admin user**: Metabase has no default password. The first visitor of the site creates the admin account.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves.
- The chart needs the CloudNativePG operator on the cluster. For a backup the platform
  team or you create a barman-cloud `ObjectStore` and name it in
  `database.backup.objectStoreName`.
- A login limited to groups needs a ClusterRole with verb `login` on `services/proxy`;
  ask the platform team for its name.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
