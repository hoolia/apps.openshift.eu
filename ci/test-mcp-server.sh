#!/bin/bash
# Render assertions for charts/mcp-server
set -u
cd "$(dirname "$0")/.."
C=charts/mcp-server
[ -d "$C/charts" ] || helm dependency build "$C" >/dev/null
fail=0
render() { helm template ledger "$C" --namespace test --set image.repository=example/ledger --set-string image.tag=1.0.0 "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }

OUT=$(render)
hasnt default "postgresql.cnpg.io"
hasnt default "DATABASE_URL"
hasnt default "cnpg.io/cluster"
hasnt default "ledger-postgres"

PG="--set postgres.enabled=true"
OUT=$(render $PG)
has pg "apiVersion: postgresql.cnpg.io/v1"
has pg "kind: Cluster"
has pg "name: ledger-postgres"
has pg "instances: 1"
has pg "enablePDB: false"
has pg "database: app"
has pg "owner: app"
has pg "size: 1Gi"
hasnt pg "storageClass:"
hasnt pg "imageName:"
has pg "memory: 512Mi"
has pg "app.kubernetes.io/component: database"
has pg "- name: DATABASE_URL"
has pg "name: ledger-postgres-app"
has pg "key: uri"
has pg "cnpg.io/cluster: ledger-postgres"
has pg "port: 5432"
has pg "kubernetes.io/metadata.name: openshift-operators"
hasnt pg "kind: ScheduledBackup"
# the database pods are not matched by the server's selector
n=$(echo "$OUT" | grep -c "^kind: NetworkPolicy$")
[ "$n" = 2 ] || { echo "FAIL pg: $n NetworkPolicies, want 2"; fail=1; }

OUT=$(render $PG --set postgres.storage.size=5Gi --set postgres.storage.storageClass=fast --set postgres.instances=2 --set postgres.envName=LEDGER_URL --set postgres.imageName=example/postgres:17)
has pgset "size: 5Gi"
has pgset "storageClass: fast"
has pgset "instances: 2"
has pgset "enablePDB: true"
has pgset "- name: LEDGER_URL"
hasnt pgset "DATABASE_URL"
has pgset "imageName: example/postgres:17"

# the Cluster renders the same with and without the CloudNativePG API known to Helm
A=$(render $PG)
B=$(render $PG --api-versions postgresql.cnpg.io/v1)
[ "$A" = "$B" ] || { echo "FAIL api-versions: render differs with --api-versions postgresql.cnpg.io/v1"; fail=1; }

OUT=$(render $PG --set networkPolicy.egress.enabled=false)
has noegress "kind: Cluster"
has noegress "name: ledger-postgres"
hasnt noegress "- Egress"

OUT=$(render $PG --set networkPolicy.enabled=false)
hasnt nonetpol "kind: NetworkPolicy"
has nonetpol "kind: Cluster"

# without an image only the RemoteMCPServer may render, never a database
OUT=$(helm template ledger "$C" --namespace test $PG 2>&1)
hasnt noimage "kind: Cluster"

OUT=$(render --set postgres.enabled=true --set postgres.bogus=1)
has schema "additional properties 'bogus' not allowed"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
