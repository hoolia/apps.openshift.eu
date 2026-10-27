#!/bin/bash
# Render assertions for charts/mcp-officeagent
set -u
cd "$(dirname "$0")/.."
C=charts/mcp-officeagent
fail=0
render() { helm template office "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
envoy() { echo "$OUT" | python3 -c 'import sys,yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d["kind"] == "ConfigMap": print(d["data"]["envoy.yaml"])'; }

# default: no token check, the server answers on the Service port itself
OUT=$(render)
has default "value: http://0.0.0.0:13080"
has default "containerPort: 13080"
has default "targetPort: mcp"
hasnt default "kind: ConfigMap"
hasnt default "name: auth"
hasnt default "checksum/auth"
hasnt default "port: 443"

# auth without an issuer: fail closed, /healthz still answers
OUT=$(render --set auth.enabled=true)
has noissuer "kind: ConfigMap"
has noissuer "value: http://127.0.0.1:13081"
has noissuer "checksum/auth"
has noissuer "image: \"docker.io/envoyproxy/envoy:"
has noissuer "port: 13080"
hasnt noissuer "port: 443"
CFG=$(envoy)
echo "$CFG" | grep -qF "status: 401" || { echo "FAIL noissuer: no 401 direct response"; fail=1; }
echo "$CFG" | grep -qF "jwt_authn" && { echo "FAIL noissuer: jwt_authn without issuer"; fail=1; }
echo "$CFG" | python3 -c 'import sys,yaml; yaml.safe_load(sys.stdin)' || { echo "FAIL noissuer: envoy.yaml is not YAML"; fail=1; }

# auth with issuer, audience and groups
OUT=$(render --set auth.enabled=true --set auth.issuerUrl=https://login.example.com/realms/demo/ \
  --set 'auth.audiences={demo-nextcloud}' --set 'auth.allowedGroups={demo-admins,demo-users}')
CFG=$(envoy)
for s in 'issuer: "https://login.example.com/realms/demo"' \
  'uri: "https://login.example.com/realms/demo/protocol/openid-connect/certs"' \
  '- "demo-nextcloud"' 'exact: "demo-admins"' 'exact: "demo-users"' \
  'address: login.example.com' 'port_value: 443' 'sni: login.example.com' \
  'path: /healthz' 'provider_name: issuer' 'port_value: 13081' 'timeout: 0s'; do
  echo "$CFG" | grep -qF -- "$s" || { echo "FAIL issuer: envoy.yaml lacks: $s"; fail=1; }
done
echo "$CFG" | grep -qF "status: 401" && { echo "FAIL issuer: deny-all route with an issuer"; fail=1; }
has issuer "port: 443"
has issuer "path: /healthz"

# explicit JWKS address with a port, plain HTTP
OUT=$(render --set auth.enabled=true --set auth.issuerUrl=http://idp.test:8080/realms/x \
  --set auth.jwksUri=http://keys.test:8081/certs)
CFG=$(envoy)
echo "$CFG" | grep -qF 'address: keys.test' || { echo "FAIL jwks: host"; fail=1; }
echo "$CFG" | grep -qF 'port_value: 8081' || { echo "FAIL jwks: port"; fail=1; }
echo "$CFG" | grep -qF 'UpstreamTlsContext' && { echo "FAIL jwks: TLS on http"; fail=1; }
has jwks "port: 8081"

# no groups: no RBAC filter
OUT=$(render --set auth.enabled=true --set auth.issuerUrl=https://login.example.com/realms/demo)
envoy | grep -qF "envoy.filters.http.rbac" && { echo "FAIL nogroups: rbac filter"; fail=1; }

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
