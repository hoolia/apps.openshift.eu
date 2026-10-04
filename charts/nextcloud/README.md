# Nextcloud

## Purpose

Nextcloud for files, calendar and contacts, with OnlyOffice for editing documents in the
browser. The chart runs Nextcloud on the upstream Nextcloud chart, a MariaDB database
through the platform's MariaDB operator, and an OnlyOffice document server.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install nextcloud grncloud/nextcloud --set nextcloud.nextcloud.host=cloud.example.org

Install one release per project. The first start takes a few minutes. Read the first
password:

    oc get secret nextcloud-credentials -o jsonpath='{.data.nextcloud-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `nextcloud.nextcloud.host` | `nextcloud.example.org` | Hostname of Nextcloud. Set it. |
| `onlyoffice.host` | empty | Hostname of OnlyOffice. Empty: `office-<hostname of Nextcloud>`. |
| `onlyoffice.enabled` | `true` | Document editing. |
| `nextcloud.persistence.nextcloudData.size` | `25Gi` | Space for user files. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `secrets.adminPassword` | empty | Password of the first administrator. Empty: generated. |
| `smtp.host` | empty | Outgoing mail server. |
| `mailu.enabled` | `true` | Run a Mailu mail server in the same project. Its values are those of the `mailu` chart, under `mailu`. |

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secrets and generates new values on
every render: set all four secrets there. A changed database password after the first
install stops the database.

To connect OnlyOffice, install the ONLYOFFICE app in Nextcloud and enter the address
`https://<hostname of OnlyOffice>` and the secret:

    oc get secret onlyoffice-credentials -o jsonpath='{.data.jwt-secret}' | base64 -d

## Delivery instructions

- Both hostnames need a DNS record pointing at the platform's ingress. A hostname under
  the platform's application domain works without a request. For your own domain, ask
  the platform team for the target and create the records at your DNS provider; the
  certificates follow automatically once the names resolve.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection, each with the value in the
  password field, and set `secrets.externalSecret.storeName` to your store.
- Mail needs DNS records and a public address: see the Delivery instructions of the
  `mailu` chart. Set `mailu.enabled=false` when you send mail through another server.
- The database is not backed up by this chart. Ask the platform team which backup covers
  your project.
