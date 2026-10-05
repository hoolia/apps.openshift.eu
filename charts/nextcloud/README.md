# Nextcloud

## Purpose

Nextcloud for files, calendar and contacts, with OnlyOffice for editing documents in the
browser. The chart runs Nextcloud on the upstream Nextcloud chart, a MariaDB database
through the platform's MariaDB operator, and an OnlyOffice document server. Around that
it runs Redis, the push server for the clients (notify_push) and the whiteboard backend.
Nextcloud Talk with its signaling, TURN and recording servers, a database backup to
object storage, monitoring and a task-processing worker are optional.

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
| `hsts.enabled` | `true` | Send the Strict-Transport-Security header `hsts.header` on every hostname of this chart. |
| `cache.enabled` | `true` | Redis for file locking and cache. Always runs when notify_push, the whiteboard or the worker is on. |
| `notifyPush.enabled` | `true` | Push server for the desktop and mobile clients, under `/push`. |
| `whiteboard.enabled` | `true` | Backend of the whiteboard app, under `/whiteboard`. |
| `taskprocessing.enabled` | `false` | Worker that runs queued tasks at once. Useful with an AI provider app. |
| `talk.enabled` | `false` | Nextcloud Talk with a signaling server, NATS, a TURN server and call recording. |
| `talk.signaling.host` | empty | Hostname of the signaling server. Empty: `signaling-<hostname of Nextcloud>`. |
| `talk.turn.host` | empty | Public DNS name of the TURN server. Empty: `turn-<hostname of Nextcloud>`. |
| `talk.turn.externalIP` | empty | Public address of the TURN server. |
| `talk.turn.service.type` | `ClusterIP` | `LoadBalancer` requests the public address. |
| `talk.turn.service.annotations` | empty | Annotations of the TURN Service, for example the address pool. |
| `talk.turn.tls.enabled` | `false` | TURN over TLS on port 5349 with a certificate for `talk.turn.host`. |
| `talk.recording.enabled` | `true` | Call recording, when Talk is on. |
| `backup.enabled` | `false` | Dump of the database to an S3 bucket. |
| `backup.storageClassName` | empty | StorageClass of the bucket claim. Required for the backup. |
| `backup.schedule` | `0 2 * * *` | Time of the backup. Empty: one backup at once. |
| `monitoring.enabled` | `false` | Exporters, ServiceMonitors and alert rules. The platform team enables it. |

A default install requests 0.75 CPU and 2.0Gi of memory. Talk adds 0.36 CPU and 0.45Gi,
the worker 0.05 CPU and 0.25Gi, monitoring 0.04 CPU and 0.13Gi.

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secrets and generates new values on
every render: set every secret of an enabled component there. A changed database
password after the first install stops the database. Use letters and digits only.

Nextcloud configures the push server, the whiteboard and Talk when its pod starts.
After you switch one of these on or off with `helm upgrade`, restart Nextcloud:

    oc rollout restart deployment/nextcloud

To connect OnlyOffice, install the ONLYOFFICE app in Nextcloud and enter the address
`https://<hostname of OnlyOffice>` and the secret:

    oc get secret onlyoffice-credentials -o jsonpath='{.data.jwt-secret}' | base64 -d

## Delivery instructions

- Every hostname needs a DNS record pointing at the platform's ingress. A hostname under
  the platform's application domain works without a request. For your own domain, ask
  the platform team for the target and create the records at your DNS provider; the
  certificates follow automatically once the names resolve.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` for the components you enable in your vault collection,
  each with the value in the password field, and set `secrets.externalSecret.storeName`
  to your store.
- Mail needs DNS records and a public address: see the Delivery instructions of the
  `mailu` chart. Set `mailu.enabled=false` when you send mail through another server.
- Talk works inside one network without more. For calls across networks the TURN server
  needs a public address:
  1. Ask the platform team for the name of the public address pool and its annotation.
  2. Set `talk.turn.service.type=LoadBalancer` and the annotation in
     `talk.turn.service.annotations`, then read the address:
     `oc get service nextcloud-turn`.
  3. Create a DNS A record for `talk.turn.host` with that address and set
     `talk.turn.externalIP` to it.
  4. The Service opens TCP and UDP port 3478 and the UDP ports
     `talk.turn.relayPorts.min` to `max`. Ask the platform team to allow them when a
     firewall sits in front of the pool.
  5. The signaling hostname needs a DNS record like the other hostnames.
- The backup covers the database only. It is a logical dump to an S3 bucket that the
  chart claims with an ObjectBucketClaim; ask the platform team which StorageClass to set
  in `backup.storageClassName`. The bucket gets a generated name. The chart reads the
  name after the claim is bound, so run `helm upgrade` a second time with the same
  values: that run creates the Backup. Check it with
  `oc get backups.k8s.mariadb.com`. User files and the Nextcloud volume are not in this
  backup. Ask the platform team which backup covers the volumes of your project.
- Monitoring creates ServiceMonitors and PrometheusRules. A tenant may not create these;
  ask the platform team to install or upgrade the release with `monitoring.enabled=true`.
