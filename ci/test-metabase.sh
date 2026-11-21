#!/bin/bash
# Render assertions for charts/metabase
set -u
cd "$(dirname "$0")/.."
C=charts/metabase
fail=0
render() { helm template t "$C" --namespace test --api-versions route.openshift.io/v1 "$@" 2>&1; }
# check <label> <python expression>; obj(kind, name) returns one rendered object
check() {
  echo "$OUT" | python3 -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def pod(): return obj("Deployment", "metabase")["spec"]["template"]["spec"]
def ctr(name):
    for c in pod()["containers"]:
        if c["name"] == name: return c
    return {}
def env(name): return {e["name"]: e.get("value") for e in ctr(name).get("env", [])}
def ref(name, var):
    return [e["valueFrom"]["secretKeyRef"] for e in ctr(name)["env"] if e["name"] == var]
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}

OUT=$(render)
check default "obj('Cluster', 'metabase-db')['spec']['bootstrap']['initdb'] == {'database': 'metabase', 'owner': 'metabase'}"
check default "obj('Cluster')['spec']['enablePDB'] is False and 'plugins' not in obj('Cluster')['spec']"
check default "ctr('metabase')['image'] == 'docker.io/metabase/metabase:v0.64.8.2'"
check default "obj('Route')['metadata']['annotations'] == {'cert-manager.io/issuer-kind': 'ClusterIssuer', 'cert-manager.io/issuer-name': 'letsencrypt'}"
check default "obj('Deployment')['spec']['selector'] == {'matchLabels': {'app.kubernetes.io/name': 'metabase'}}"
check default "[c['name'] for c in pod()['containers']] == ['metabase']"
check default "env('metabase')['MB_JETTY_HOST'] == '0.0.0.0' and 'MB_SITE_URL' not in env('metabase')"
check default "obj('Service')['spec']['ports'][0]['port'] == 3000"
check default "not objs('ServiceAccount') and not objs('RoleBinding') and not objs('NetworkPolicy')"
check default "ref('metabase', 'MB_DB_PASS') == [{'name': 'metabase-db-app', 'key': 'password'}]"
check default "set(obj('Secret', 'metabase')['data']) == {'MB_ENCRYPTION_SECRET_KEY', 'cookie-secret'}"
check default "all(c['securityContext']['runAsNonRoot'] for c in pod()['containers'])"

OUT=$(render --set oauthProxy.enabled=true --set route.hostname=bi.example.org)
check proxy "[c['name'] for c in pod()['containers']] == ['metabase', 'oauth-proxy']"
check proxy "env('metabase')['MB_JETTY_HOST'] == '127.0.0.1' and env('metabase')['MB_SITE_URL'] == 'https://bi.example.org'"
check proxy "obj('Service')['spec']['ports'][0]['port'] == 4180 and pod()['serviceAccountName'] == 'metabase'"
check proxy "'\"verb\":\"get\"' in ' '.join(ctr('oauth-proxy')['args']) and not objs('RoleBinding')"
check proxy "obj('NetworkPolicy', 'metabase')['spec']['ingress'][0]['ports'] == [{'protocol': 'TCP', 'port': 4180}]"

OUT=$(render --set oauthProxy.enabled=true --set 'oauthProxy.allowedGroups={a,b}' --set oauthProxy.loginClusterRole=login)
check groups "[s['name'] for s in obj('RoleBinding', 'metabase-app-login')['subjects']] == ['a', 'b']"
check groups "obj('RoleBinding')['roleRef']['name'] == 'login'"
check groups "'\"verb\":\"login\"' in ' '.join(ctr('oauth-proxy')['args'])"
render --set oauthProxy.enabled=true --set 'oauthProxy.allowedGroups={a}' | grep -q 'loginClusterRole is required' \
  || { echo "FAIL groups: allowedGroups without loginClusterRole renders"; fail=1; }

OUT=$(render --set secrets.existingSecret=mine --set oauthProxy.enabled=true)
check existingSecret "not objs('Secret') and not objs('ExternalSecret')"
check existingSecret "ref('metabase', 'MB_ENCRYPTION_SECRET_KEY') == [{'name': 'mine', 'key': 'MB_ENCRYPTION_SECRET_KEY'}]"
check existingSecret "ref('oauth-proxy', 'OAUTH2_PROXY_COOKIE_SECRET') == [{'name': 'mine', 'key': 'cookie-secret'}]"

OUT=$(render --set database.backup.enabled=true --set database.backup.objectStoreName=store)
check backup "obj('Cluster')['spec']['plugins'][0]['parameters'] == {'barmanObjectName': 'store'}"
check backup "obj('ScheduledBackup', 'metabase-db-scheduled')['spec']['method'] == 'plugin'"

OUT=$(render --set-json 'connectsTo=[{"apiVersion":"apps/v1","kind":"StatefulSet","name":"ch"}]')
check connectsTo "[t['name'] for t in json.loads(obj('Deployment')['metadata']['annotations']['app.openshift.io/connects-to'])] == ['metabase-db', 'ch']"

[ $fail = 0 ] && echo "PASS metabase" || exit 1
