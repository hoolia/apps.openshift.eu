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

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
