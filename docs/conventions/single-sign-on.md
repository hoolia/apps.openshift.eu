# Single sign-on

A Deployment or StatefulSet labelled `keycloak.hoolia.eu/sso: "true"` gets a login client
from the platform. The platform creates, in the same project, a KeycloakClient and a Secret
named `public-<name>`, where `<name>` is the name of the labelled object. The client lives in
realm `public` at `https://login.openshift.eu`, for the host of the Route of the same name.
The app reads the Secret and sets up its login with it. Charts that do this for you, such as
`nextcloud`, switch it on with one value; see the chart's "How to login" section.

## What the app needs

| Object | Name | Requirement |
|---|---|---|
| Deployment or StatefulSet | `<name>` | label `keycloak.hoolia.eu/sso: "true"` |
| Route | `<name>`, the same name | admitted by the router for this project; its host becomes the client ID |

The platform acts when the label is set and again when the router admits a host for the
Route. A host that another project holds, or that already has a client, gets no client: one
client per host.

## What the platform creates

Secret `public-<name>`:

| Key | Value |
|---|---|
| `sso-client-id` | the host of the Route, for example `myapp.apps.example.org` |
| `sso-client-secret` | generated once and kept |
| `sso-discovery-uri` | `https://login.openshift.eu/realms/public/.well-known/openid-configuration` |
| `sso-issuer-url` | `https://login.openshift.eu/realms/public` |
| `sso-groups-regex` | `^(admin\|<tenant>-admins)$`, the groups that may log in as administrator |

KeycloakClient `public-<name>` allows redirects to any path of `https://<host>/` and puts
the roles of the tenant (`<tenant>-*`) in the `groups` claim of the token. The project cannot
change or delete the client. Removing the label removes the client and the Secret; deleting
the Secret makes the platform write a new one with a new client secret.

## Example

An OAuth2 proxy that answers `200` after a login. It needs its own cookie secret of 32
bytes:

    oc -n <project> create secret generic myapp-cookie --from-literal=cookie-secret=$(openssl rand -hex 16)
    oc -n <project> apply -f - <<'EOF'
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: myapp
      labels:
        keycloak.hoolia.eu/sso: "true"
    spec:
      selector:
        matchLabels:
          app: myapp
      template:
        metadata:
          labels:
            app: myapp
        spec:
          containers:
          - name: myapp
            image: quay.io/oauth2-proxy/oauth2-proxy:v7.12.0
            args:
            - --provider=oidc
            - --oidc-issuer-url=$(OIDC_ISSUER_URL)
            - --client-id=$(OIDC_CLIENT_ID)
            - --client-secret=$(OIDC_CLIENT_SECRET)
            - --cookie-secret=$(COOKIE_SECRET)
            - --redirect-url=https://$(OIDC_CLIENT_ID)/oauth2/callback
            - --email-domain=*
            - --http-address=0.0.0.0:8080
            - --upstream=static://200
            env:
            - name: OIDC_ISSUER_URL
              valueFrom: {secretKeyRef: {name: public-myapp, key: sso-issuer-url, optional: true}}
            - name: OIDC_CLIENT_ID
              valueFrom: {secretKeyRef: {name: public-myapp, key: sso-client-id, optional: true}}
            - name: OIDC_CLIENT_SECRET
              valueFrom: {secretKeyRef: {name: public-myapp, key: sso-client-secret, optional: true}}
            - name: COOKIE_SECRET
              valueFrom: {secretKeyRef: {name: myapp-cookie, key: cookie-secret}}
            ports:
            - containerPort: 8080
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: myapp
    spec:
      selector:
        app: myapp
      ports:
      - port: 8080
        targetPort: 8080
    ---
    apiVersion: route.openshift.io/v1
    kind: Route
    metadata:
      name: myapp
    spec:
      to:
        kind: Service
        name: myapp
      port:
        targetPort: 8080
      tls:
        termination: edge
    EOF

## Verify it worked

    oc -n <project> get route myapp -o jsonpath='{.status.ingress[0].conditions[0].status}{"\n"}'
    oc -n <project> extract secret/public-myapp --keys=sso-client-id --to=-

The first command prints `True` once the router admitted the host. The second prints that
host within a minute after. The pod reads the Secret at its start: restart it once the
Secret exists (`oc -n <project> rollout restart deployment/myapp`).
