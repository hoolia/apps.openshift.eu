#!/bin/bash
# Render assertions for charts/matomo
set -u
cd "$(dirname "$0")/.."
C=charts/matomo
fail=0
render() { helm template t "$C" --namespace test --api-versions route.openshift.io/v1 "$@" 2>&1; }
# check <label> <python expression>; obj(kind, name) returns one rendered object
check() {
  echo "$OUT" | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def pod(): return obj("Deployment", "matomo")["spec"]["template"]["spec"]
def ctr(name):
    for c in pod()["containers"]:
        if c["name"] == name: return c
    return {}
def ref(name, var):
    return [e["valueFrom"]["secretKeyRef"] for e in ctr(name)["env"] if e["name"] == var]
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}

OUT=$(render)
# names that hold data never change between versions
check default "obj('PersistentVolumeClaim', 'matomo-config') and obj('MariaDB', 'matomo-db')"
check default "obj('PersistentVolumeClaim', 'matomo-config')['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'"
check default "obj('Deployment', 'matomo')['spec']['selector'] == {'matchLabels': {'app.kubernetes.io/name': 'matomo'}}"
check default "obj('Deployment', 'matomo')['spec']['strategy']['type'] == 'Recreate'"
check default "ctr('matomo')['image'] == 'docker.io/library/matomo:5.14.2-fpm-alpine'"
check default "ctr('nginx')['image'] == 'docker.io/nginxinc/nginx-unprivileged:1.31.7-alpine'"
check default "obj('MariaDB')['spec']['image'] == 'docker.io/mariadb:11.4.10'"
check default "all(c['securityContext']['runAsNonRoot'] and c['securityContext']['capabilities']['drop'] == ['ALL'] for c in pod()['containers'])"
# the code copy keeps an installed config and rebuilds matomo.js
check default "'[ -e matomo.php ] || cp -R /usr/src/matomo/. /var/www/html/' in ctr('matomo')['command'][2]"
check default "'custom-matomo-js:update' in ctr('matomo')['command'][2]"
check default "ref('matomo', 'MATOMO_DATABASE_PASSWORD') == [{'name': 'matomo-db', 'key': 'password'}]"
check default "obj('Secret', 'matomo-db')['type'] == 'Opaque' and set(obj('Secret', 'matomo-db')['data']) == {'password', 'root-password'}"
check default "not objs('Backup') and not objs('ExternalSecret')"
check default "'host' not in obj('Route', 'matomo')['spec'] and obj('Route')['spec']['path'] == '/'"
check default "obj('Route')['metadata']['annotations'] == {'cert-manager.io/issuer-kind': 'ClusterIssuer', 'cert-manager.io/issuer-name': 'letsencrypt'}"

OUT=$(render --set secrets.existingSecret=mine)
check existingSecret "not objs('Secret') and not objs('ExternalSecret')"
check existingSecret "ref('matomo', 'MATOMO_DATABASE_PASSWORD') == [{'name': 'mine', 'key': 'password'}]"
check existingSecret "obj('MariaDB')['spec']['rootPasswordSecretKeyRef']['name'] == 'mine'"

OUT=$(render --set secrets.type=externalSecret --set secrets.externalSecret.storeName=s)
check externalSecret "obj('ExternalSecret', 'matomo-db')['spec']['target']['name'] == 'matomo-db'"
check externalSecret "not objs('Secret')"

OUT=$(render --set database.backup.enabled=true --set route.hostname=a.example.org \
  --set route.labels.shard=public --set-string podLabels.mesh=false)
check backup "obj('Backup', 'matomo-db-backup')['spec']['schedule']['cron'] == '0 2 * * *'"
check backup "obj('Backup')['spec']['storage']['persistentVolumeClaim']['resources']['requests']['storage'] == '5Gi'"
check backup "obj('Backup')['metadata']['annotations']['argocd.argoproj.io/ignore-healthcheck'] == 'true'"
check route "obj('Route')['spec']['host'] == 'a.example.org' and obj('Route')['metadata']['labels']['shard'] == 'public'"
check podLabels "obj('Deployment')['spec']['template']['metadata']['labels'] == {'app.kubernetes.io/name': 'matomo', 'mesh': 'false'}"

[ $fail = 0 ] && echo "PASS matomo" || exit 1
