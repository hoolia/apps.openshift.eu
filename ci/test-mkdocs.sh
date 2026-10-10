#!/bin/bash
# Render assertions for charts/mkdocs
set -u
cd "$(dirname "$0")/.."
C=charts/mkdocs
fail=0
render() { helm template mkdocs "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }

OUT=$(render)
for k in BuildConfig ImageStream Deployment Service Route ConfigMap; do has default "kind: $k"; done
has default "mkdocs new /tmp/src"
has default "type: Dockerfile"
has default "targetPort: 8080"
has default "memory: 1Gi"
hasnt default "kind: CronJob"
hasnt default "kind: NetworkPolicy"
hasnt default "kind: Secret"
# no image trigger: under Argo CD it fights over the image field
hasnt default "image.openshift.io/triggers"
has default "image: image-registry.openshift-image-registry.svc:5000/test/mkdocs:latest"
has default "imagePullPolicy: Always"
has default "location = /healthz"
has default "path: /healthz"
hasnt default "deny all;"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.contextDir=site --set source.requirementsFile=req.txt)
has git "uri: https://example.com/docs.git"
has git "contextDir: site"
has git "COPY --chown=1001:0 . /tmp/src"
has git "pip install --no-cache-dir -r req.txt"
has git "mkdocs build --strict -f mkdocs.yml"
hasnt git "sourceSecret"
hasnt git "mkdocs new"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.strict=false)
hasnt nostrict "--strict"

OA="--set oauth.enabled=true --set oauth.issuerUrl=https://login.example.com/realms/x --set oauth.clientId=docs --set secrets.clientSecret=s3cr3t"

OUT=$(render --set oauth.enabled=true --set oauth.clientId=docs --set secrets.clientSecret=s3cr3t)
has "oauth without issuer" "oauth.issuerUrl is required"
OUT=$(render --set oauth.enabled=true --set oauth.issuerUrl=https://login.example.com/realms/x --set secrets.clientSecret=s3cr3t)
has "oauth without client" "oauth.clientId is required"
OUT=$(render --set oauth.enabled=true --set oauth.issuerUrl=https://login.example.com/realms/x --set oauth.clientId=docs)
has "oauth without secret" "secrets.clientSecret is required"

OUT=$(render $OA --set route.hostname=docs.example.com --set 'oauth.allowedGroups={staff}')
has oauth "name: oauth2-proxy"
has oauth "--oidc-issuer-url=https://login.example.com/realms/x"
has oauth "--redirect-url=https://docs.example.com/oauth2/callback"
has oauth "--upstream=http://127.0.0.1:8080"
has oauth "--allowed-group=staff"
has oauth "kind: NetworkPolicy"
has oauth "targetPort: 4180"
has oauth "name: mkdocs-oauth"
hasnt oauth "s3cr3t"
# other policies of the cluster can open the site port: nginx itself refuses all but the proxy
has oauth "allow 127.0.0.1;"
has oauth "deny all;"

# a secret generated elsewhere can have any length; oauth2-proxy wants 16, 24 or 32 bytes
cookie() { render $OA --set secrets.cookieSecret="$1" | awk '/cookie-secret:/{gsub(/"/,"",$2); print $2}' | base64 -d; }
C48=e9e2334228689ae870e4f029424641eabcf5c9efbc2d3772
[ "$(cookie $C48 | wc -c)" = 32 ] || { echo "FAIL cookie: 48 characters do not give 32"; fail=1; }
[ "$(cookie $C48)" = "$(cookie $C48)" ] || { echo "FAIL cookie: not stable between renders"; fail=1; }
C32=abcdefghijklmnopqrstuvwxyz012345
[ "$(cookie $C32)" = "$C32" ] || { echo "FAIL cookie: a 32 character value is not kept"; fail=1; }

OUT=$(render $OA --set-json 'oauth.allowedGroups=[""]')
has "empty group" "name: oauth2-proxy"
hasnt "empty group" "--allowed-group"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.private=true --set source.gitUsername=u --set secrets.gitPassword=pw)
has private "sourceSecret"
has private "type: kubernetes.io/basic-auth"

OUT=$(render $OA --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test --set source.gitRepo=https://example.com/docs.git --set source.private=true)
has external "kind: ExternalSecret"
has external "key: mkdocs-client-secret"
has external "key: mkdocs-git-password"
hasnt external "kind: Secret"

OUT=$(render --set source.gitRepo=https://example.com/docs.git)
for k in CronJob ServiceAccount Role RoleBinding; do has poller "kind: $k"; done
has poller "buildconfigs/instantiate"
# the poller restarts the Deployment after a build
has poller 'resources: ["deployments"]'
has poller "name: DEPLOYMENT"
has poller 'schedule: "*/10 * * * *"'
# poll.py itself names GIT_PASSWORD, so assert on the secret reference
hasnt poller "key: password"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.private=true)
has "poller private" "key: password"

# a version tag such as 1.0 must stay a string
OUT=$(render --set source.gitRepo=https://example.com/docs.git --set-string source.gitRef=1.0)
has "numeric ref" 'value: "1.0"'
has "numeric ref" 'ref: "1.0"'

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.pollSchedule=)
hasnt "poller off" "kind: CronJob"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.webhook=true --set source.pollSchedule=)
for t in GitLab GitHub Generic; do has webhook "type: $t"; done
has webhook "name: mkdocs-webhook"
has webhook "WebHookSecretKey"
has webhook "name: system:webhook"
has webhook "name: system:unauthenticated"
has webhook "image.openshift.io/triggers"
hasnt webhook "kind: CronJob"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.webhook=true --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test)
has "webhook external" "key: mkdocs-webhook-secret"
hasnt "webhook external" "kind: Secret"

# without a repository there is nothing to push to
OUT=$(render --set source.webhook=true)
hasnt "webhook without repo" "type: GitLab"
hasnt "webhook without repo" "system:webhook"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
