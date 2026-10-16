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
- `connections` (default: none): Connections the chart manages. Each has `name`, `protocol`, `hostname`, `port`, `parameters` (Guacamole connection parameters, values as strings), `secretParameters` (parameter name to a key of the Secret `<release>-guacamole`, for example `password: vncPassword`) and `users`. `disable-copy` and `disable-paste` are `"true"` unless `parameters` sets them, so no clipboard crosses a connection by default. The chart sets exactly these parameters and gives exactly these users READ on the connection, with no other permission. A connection removed from the list stays in the database; empty its `users` to close it.
- `oidc.enabled` (default: false): Sign in at an OpenID Connect issuer.
- `oidc.issuerUrl`, `oidc.clientId`: Your issuer and client. Required with `oidc.enabled`, together with `route.hostname`.
- `oidc.authorizationEndpoint`, `oidc.jwksEndpoint` (default: the Keycloak addresses under `oidc.issuerUrl`): Endpoints of another issuer.
- `oidc.usernameClaim` (default: preferred_username): Token claim that holds the user name. It must equal the names in `connections[].users`.
- `oidc.autoRedirect` (default: false): Send users straight to the issuer. With false they see the login form with a link to the issuer, and `guacadmin` can still sign in.
- `recording.enabled` (default: true): Record every session of a managed connection to a volume of `recording.size` (default: 10Gi). The administrator plays a recording from the connection history.
- `recording.s3.enabled` (default: false): Copy the recordings every 5 minutes to a bucket from an ObjectBucketClaim of class `recording.s3.storageClassName`. A recording is never deleted from the bucket by the chart.
- `watermark.enabled` (default: false): Tile the signed-in user name and the time (UTC, refreshed every minute) faintly over every page, remote desktop included, so a photo or screenshot of the screen shows who took it and when. The overlay lets every click and key through. The client address is not shown: the browser does not know it. The extension is `files/watermark.jar`, built by `ci/build-watermark.sh` from `files/watermark`.
- `kiosk.enabled` (default: false): A Firefox desktop in kiosk mode behind a VNC server, Service `<release>-kiosk` on port 5900, reachable from the Guacamole pod only. Add a connection with `protocol: vnc`, `hostname: <release>-kiosk`, `port: 5900` and `secretParameters: {password: vncPassword}`. The image is built in the project from `files/kiosk/Containerfile` (Fedora, Firefox, TigerVNC) unless `kiosk.image` names a prebuilt one.
- `kiosk.homepage` (default: about:blank): First page.
- `kiosk.allowedUrls` (default: none): The only addresses Firefox opens, as match patterns such as `https://code.example.com/*`. Everything else, `file://` included, is blocked. Firefox policy also turns off developer tools, `about:config`, printing, extensions, password reveal and Firefox accounts, and puts downloads in a temporary directory of the pod, which Firefox cannot open. Firefox has no policy that refuses downloads.
- `kiosk.egressFirewall.enabled` (default: true), `kiosk.egressFirewall.allowedDnsNames`, `kiosk.egressFirewall.allowedCidrs` (default: none): An EgressFirewall named `default` that lets pods of the project reach only these names and address ranges outside the cluster.
- `kiosk.profileSize` (default: 1Gi): Volume of the Firefox profile, which keeps saved logins across restarts.
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
- The kiosk EgressFirewall applies to every pod of the project, not only to the desktop, and a project holds one EgressFirewall only. With `oidc.enabled`, the issuer host (and the host of `oidc.jwksEndpoint`, when set) must be in `kiosk.egressFirewall.allowedDnsNames`, otherwise Guacamole cannot fetch the signing keys; the chart refuses to render without it. With `recording.s3.enabled`, the bucket endpoint comes from the ObjectBucketClaim at run time, so the chart cannot check it: when the endpoint is outside the cluster, add its host or address range yourself, or the copy to the bucket fails. Creating an EgressFirewall can need a permission your project role lacks: ask the platform team, or set `kiosk.egressFirewall.enabled=false` and have the platform team provide it.
- Do not save the password of a single sign-on account in the kiosk. An operator signs that account in once in a desktop session instead, and the kiosk keeps the session. Saved logins are only for accounts of systems that are reachable from inside the kiosk's network alone. Firefox fills them in without showing the password, but it has no policy that hides the saved-logins page `about:logins` while keeping autofill (`PasswordManagerEnabled=false` also stops autofill), so a user who reaches that page can copy or export a saved password. The kiosk has no address bar and no menus; check by keyboard in a session that `about:logins` cannot be opened before you rely on it.
- With `secrets.type=externalSecret`, create the items named in `secrets.externalSecret.items` in your vault collection, each with the value in the password field, and set `secrets.externalSecret.storeName` to your store.
