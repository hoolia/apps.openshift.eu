# guacamole

## Purpose

Runs Apache Guacamole, a remote desktop gateway in the browser: users open VNC, RDP or SSH desktops from a web page and receive only pixels. The chart brings the web application, the guacd proxy, a PostgreSQL database (CloudNativePG) and the connections themselves, which are declared in values. Optionally users sign in at an OpenID Connect issuer, and every session is recorded, played back from the connection history and copied to object storage.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install guacamole grncloud/guacamole
```

Read the address and the administrator password:

```bash
oc get route guacamole-guacamole -o jsonpath='https://{.spec.host}/{"\n"}'
oc get secret guacamole-guacamole -o jsonpath='{.data.adminPassword}' | base64 -d
```

Objects are named `<release>-guacamole`. The administrator is `guacadmin`; its password is set from the Secret on every install and upgrade, so a password changed in the web interface is reset.

- `route.hostname` (default: empty): Hostname of Guacamole. Empty: OpenShift assigns one. Set it at install; a tenant cannot change the hostname of an existing Route.
- `connections` (default: none): Connections the chart manages. Each has `name`, `protocol`, `hostname`, `port`, `parameters` (Guacamole connection parameters, values as strings), `secretParameters` (parameter name to a key of the Secret `<release>-guacamole`, for example `password: vncPassword`) and `users`. The chart sets exactly these parameters and gives exactly these users READ on the connection, with no other permission. A connection removed from the list stays in the database; empty its `users` to close it.
- `oidc.enabled` (default: false): Sign in at an OpenID Connect issuer.
- `oidc.issuerUrl`, `oidc.clientId`: Your issuer and client. Required with `oidc.enabled`, together with `route.hostname`.
- `oidc.authorizationEndpoint`, `oidc.jwksEndpoint` (default: the Keycloak addresses under `oidc.issuerUrl`): Endpoints of another issuer.
- `oidc.usernameClaim` (default: preferred_username): Token claim that holds the user name. It must equal the names in `connections[].users`.
- `oidc.autoRedirect` (default: false): Send users straight to the issuer. With false they see the login form with a link to the issuer, and `guacadmin` can still sign in.
- `recording.enabled` (default: true): Record every session of a managed connection to a volume of `recording.size` (default: 10Gi). The administrator plays a recording from the connection history.
- `recording.s3.enabled` (default: false): Copy the recordings every 5 minutes to a bucket from an ObjectBucketClaim of class `recording.s3.storageClassName`. A recording is never deleted from the bucket by the chart.
- `database.instances` (default: 1), `database.size` (default: 1Gi): The PostgreSQL cluster.
- `secrets.adminPassword`, `secrets.vncPassword` (default: empty): Generated once when empty.
- `secrets.type` (default: kubernetes): `externalSecret` reads the secrets from a secret store.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the platform's ingress. Ask the platform team for the record; the certificate follows automatically once it resolves.
- Guacamole uses the OpenID Connect implicit flow and needs no client secret. The client at the issuer must allow the implicit flow and the redirect address `https://<hostname>/`. Ask the platform team for a client at the platform's issuer, or use your own issuer.
- A user signed in through the issuer sees only the connections whose `users` list holds their name from `oidc.usernameClaim`. Any other user of the issuer can sign in and sees no connection.
- The connections run on each install and upgrade through a Job that runs after the other objects; under Argo CD it is a PostSync hook. A change in the web interface to a managed connection is undone by the next run.
- With `recording.s3.enabled`, give the bucket a lifecycle rule that expires recordings after `recording.s3.expiryDays` days; an ObjectBucketClaim cannot carry one. With the credentials from the Secret and ConfigMap `<release>-guacamole-recordings`:
  `aws s3api put-bucket-lifecycle-configuration --endpoint-url <endpoint> --bucket <BUCKET_NAME> --lifecycle-configuration '{"Rules":[{"ID":"expire","Status":"Enabled","Filter":{},"Expiration":{"Days":180}}]}'`
- The recordings volume and the bucket are kept when the chart is uninstalled. Delete them yourself when the recordings are no longer needed.
- With `secrets.type=externalSecret`, create the items named in `secrets.externalSecret.items` in your vault collection, each with the value in the password field, and set `secrets.externalSecret.storeName` to your store.
