# Mailu

## Purpose

Mailu is a mail server: SMTP, IMAP and POP3, webmail (Roundcube), a spam filter (rspamd)
and an admin interface. This chart wraps the upstream chart `mailu` 2.6.3 and adds what a
project on OpenShift needs: a Route for webmail, a public Service for the mail protocols,
NetworkPolicies, a spam filter configuration and, on request, a certificate, monitoring
and a page to copy mailboxes from another server (imapsync).

The upstream chart is included under the alias `upstream`. Every value of the upstream
chart is set below `upstream:`, for example `upstream.domain`. A chart that includes this
chart as `mailu` sets the same value as `mailu.upstream.domain`.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install mailu grncloud/mailu \
      --set upstream.domain=example.org \
      --set upstream.initialAccount.domain=example.org \
      --set 'upstream.hostnames={mail.example.org}'

Read the webmail address and the password of the first account (`admin@<domain>`):

    oc get route mailu -o jsonpath='https://{.spec.host}{"\n"}'
    oc get secret mailu-secrets -o jsonpath='{.data.initial-account-password}' | base64 -d

| Value | Default | Meaning |
|---|---|---|
| `upstream.domain` | `example.org` | Mail domain, the part after the @. |
| `upstream.initialAccount.domain` | `example.org` | Domain of the first admin account. Same value as the mail domain. |
| `upstream.hostnames` | `[mail.example.org]` | Hostnames of the mail server. The first one goes in the MX record. |
| `upstream.persistence.size` | `20Gi` | Size of the one volume that holds all mail and state. |
| `upstream.persistence.storageClass` | empty | Empty: the default StorageClass. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the four secrets from a secret store. |
| `secrets.initialAdminPassword` | empty | Password of the first admin account. Empty: generated. |
| `mailService.type` | `ClusterIP` | `LoadBalancer` gives the mail protocols a public address. |
| `mailService.annotations` | `{}` | Annotations of that Service, for example the address pool. |
| `mailService.ports.*` | mail ports on | Ports of that Service. `http` and `https` are off. |
| `egressService.enabled` | `false` | Second public address, used as the source of outgoing mail. |
| `route.hostname` | empty | Hostname of webmail. Empty: OpenShift assigns one. |
| `certificate.enabled` | `false` | Request a certificate for `upstream.hostnames` from `certificate.issuerName`. |
| `monitoring.enabled` | `false` | ServiceMonitors and alert rules. Needs the right to create them. |
| `imapsync.enabled` | `false` | Page to copy mailboxes from another mail server. |

Web access goes through the Route: the router ends TLS and sends plain HTTP to the mail
front end. Webmail is at `/webmail`, the admin interface at `/admin`. To serve the web
pages on the mail address itself, set `mailService.ports.http` and
`mailService.ports.https` to true.

Without `certificate.enabled` the mail ports use a certificate of the cluster's internal
service CA. Mail clients and other mail servers do not trust it. Set `certificate.enabled`
before real use.

Secrets left empty are generated at the first install and kept on every upgrade with
`helm upgrade`. A tool that renders the chart without access to the cluster, such as
Argo CD or `helm template`, cannot read the existing Secret and generates new values on
every render: set `secrets.secretKey`, `secrets.apiToken`, `secrets.roundcubeKey` and
`secrets.initialAdminPassword` there.

All pods that share the mail volume run on one node, because the volume is ReadWriteOnce.
The default requests add up to about 0.5 CPU and 2.2Gi of memory.

Install one release per project: the object names start with `mailu`, not with the
release name.

## Delivery instructions

The platform team does these steps for you. Ask for them in one request and name your
project, the mail domain and the hostname of the mail server.

- Public address. The mail Service gets a public IP address only from the public address
  pool. Ask which annotation selects that pool and set it, for example
  `--set 'mailService.annotations.metallb\.io/address-pool=<pool>'`. Read the address with
  `oc get service mailu-front-ext`.
- Inbound port 25. Ask the platform team to confirm that port 25 from the internet
  reaches the public range. Without it you can send mail but not receive it.
- Outgoing address. Outgoing mail must leave from a public address with reverse DNS. Ask
  whether the platform sends the mail of your pods from the address of the mail Service.
  If it does not, set `egressService.enabled=true` with the same pool annotation; outgoing
  mail then leaves from the address of `mailu-postfix-egress`.
- DNS records, all created by the platform team:
  - `A`: the mail hostname to the address of `mailu-front-ext`.
  - `MX`: the mail domain to the mail hostname.
  - `TXT` (SPF) on the mail domain: `v=spf1 mx a:<mail hostname> -all`. Add
    `ip4:<outgoing address>` when the outgoing address differs.
  - `TXT` (DKIM): `dkim._domainkey.<mail domain>`. Generate the key in the admin
    interface under Mail domains, Details, and hand over the record shown there.
  - `TXT` (DMARC): `_dmarc.<mail domain>`, for example
    `v=DMARC1; p=reject; rua=mailto:postmaster@<mail domain>`.
  - Reverse DNS (`PTR`): the outgoing address to the mail hostname.
  - Autoconfig, if used: `CNAME` `autoconfig.<mail domain>` to the mail hostname, and
    `mailService.ports.http` and `https` set to true.
- Admin pod. The upstream chart always adds the capability `NET_BIND_SERVICE` to the
  admin container and the container must start as root. The `anyuid` SCC allows root but
  no added capability. Ask the platform team to confirm that the admin pod can run in
  your project before you rely on this chart.
- Webmail hostname. A `route.hostname` outside the default application domain needs a DNS
  record pointing at the platform's ingress.
- Certificate. Ask for the name of the issuer and set `certificate.enabled=true` and
  `certificate.issuerName`. The certificate is issued once the DNS records exist.
- Monitoring. `monitoring.enabled=true` needs the role `monitoring-edit` in your project.
- With `secrets.type=externalSecret`, create four items in your vault collection, named
  as in `secrets.externalSecret.items`, each with the value in the password field, and
  set `secrets.externalSecret.storeName` to your store.
