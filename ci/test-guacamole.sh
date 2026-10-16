#!/bin/bash
# Render assertions for charts/guacamole
set -u
cd "$(dirname "$0")/.."
C=charts/guacamole
fail=0
render() { helm template t "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
# check <label> <python expression>; obj(kind, name) returns one rendered object,
# env(container) the env of a Deployment container as a dict of plain values
check() {
  echo "$OUT" | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def pod(): return obj("Deployment", "t-guacamole")["spec"]["template"]["spec"]
def ctr(name):
    for c in pod()["containers"]:
        if c["name"] == name: return c
    return {}
def env(name): return {e["name"]: e.get("value") for e in ctr(name).get("env", [])}
def mounts(name): return [m["name"] for m in ctr(name).get("volumeMounts", [])]
def sql(): return obj("ConfigMap", "t-guacamole-init")["data"]["init.sql"]
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}

NAMESPACED="['ConfigMap','Deployment','ExternalSecret','Job','NetworkPolicy','ObjectBucketClaim','PersistentVolumeClaim','Cluster','Route','Secret','Service']"

OUT=$(render)
check default "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd']"
check default "'recordings' in mounts('guacamole') and 'recordings' in mounts('guacd')"
check default "env('guacamole')['RECORDING_ENABLED'] == 'true'"
check default "env('guacamole')['RECORDING_SEARCH_PATH'] == '/var/lib/guacamole/recordings'"
check default "env('guacamole')['GUACD_HOSTNAME'] == 'localhost'"
check default "env('guacamole')['WEBAPP_CONTEXT'] == 'ROOT'"
check default "env('guacamole')['POSTGRESQL_ENABLED'] == 'true'"
check default "[e['valueFrom']['secretKeyRef'] for e in ctr('guacamole')['env'] if e['name'] == 'POSTGRESQL_PASSWORD'] == [{'name': 't-guacamole-db-app', 'key': 'password'}]"
check default "'OPENID_ENABLED' not in env('guacamole')"
check default "ctr('guacamole')['image'] == 'guacamole/guacamole:1.6.0' and ctr('guacd')['image'] == 'guacamole/guacd:1.6.0'"
# guacd authenticates nobody: only the web container in the same pod may reach it
check default "'127.0.0.1' in ctr('guacd')['command']"
check default "'fsGroup' not in pod().get('securityContext', {}) and 'runAsUser' not in pod().get('securityContext', {})"
check default "obj('Deployment')['spec']['strategy']['type'] == 'Recreate'"
check default "obj('Service', 't-guacamole')['spec']['ports'][0]['port'] == 8080"
check default "obj('Cluster', 't-guacamole-db')['spec']['instances'] == 1"
check default "obj('PersistentVolumeClaim', 't-guacamole-recordings')['spec']['accessModes'] == ['ReadWriteOnce']"
check default "obj('Route')['spec']['tls']['termination'] == 'edge'"
check default "set(obj('Secret', 't-guacamole')['data']) == {'adminPassword', 'vncPassword'}"
check default "obj('Job', 't-guacamole-init')['metadata']['annotations']['helm.sh/hook'] == 'post-install,post-upgrade'"
check default "'/opt/guacamole/bin/initdb.sh --postgresql' in ' '.join(obj('Job')['spec']['template']['spec']['initContainers'][0]['command'])"
check default "'guacamole_user' in obj('ConfigMap', 't-guacamole-init')['data']['init.sh']"
check default "'UPDATE guacamole_user' in sql() and \"name = 'guacadmin'\" in sql()"
check default "obj('NetworkPolicy')['spec']['ingress'] == [{'from': [{'namespaceSelector': {'matchLabels': {'network.openshift.io/policy-group': 'ingress'}}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]"
check default "not objs('ObjectBucketClaim') and not objs('ExternalSecret')"
check default "all(d['kind'] in $NAMESPACED for d in docs)"
check default "all('namespace' not in d['metadata'] or d['metadata']['namespace'] == 'test' for d in docs)"

CONN='connections=[{"name":"desk","protocol":"vnc","hostname":"kiosk","port":5900,"parameters":{"disable-copy":"true","disable-paste":"true","recording-include-keys":"false","color-depth":"24"},"secretParameters":{"password":"vncPassword"},"users":["guest@example.com","o'"'"'brien@example.com"]}]'
FULL="--set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set oidc.clientId=desk --set route.hostname=desk.example.com --set recording.s3.enabled=true --set recording.s3.storageClassName=objects"

OUT=$(render $FULL --set-json "$CONN")
check full "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd', 'rclone']"
check full "all('recordings' in mounts(c) for c in ['guacamole', 'guacd', 'rclone'])"
check full "ctr('rclone')['image'] == 'rclone/rclone:1.71'"
check full "'rclone copy' in ' '.join(ctr('rclone')['args']) and 'rclone sync' not in ' '.join(ctr('rclone')['args'])"
check full "'sleep 300' in ' '.join(ctr('rclone')['args'])"
check full "{e['valueFrom']['secretKeyRef']['name'] for e in ctr('rclone')['env'] if 'valueFrom' in e and 'secretKeyRef' in e['valueFrom']} == {'t-guacamole-recordings'}"
check full "{e['valueFrom']['configMapKeyRef']['name'] for e in ctr('rclone')['env'] if 'valueFrom' in e and 'configMapKeyRef' in e['valueFrom']} == {'t-guacamole-recordings'}"
check full "obj('ObjectBucketClaim', 't-guacamole-recordings')['spec']['storageClassName'] == 'objects'"
check full "env('guacamole')['OPENID_ENABLED'] == 'true'"
check full "env('guacamole')['OPENID_USERNAME_CLAIM_TYPE'] == 'preferred_username'"
check full "env('guacamole')['OPENID_ISSUER'] == 'https://login.example.com/realms/x'"
check full "env('guacamole')['OPENID_CLIENT_ID'] == 'desk'"
check full "env('guacamole')['OPENID_REDIRECT_URI'] == 'https://desk.example.com/'"
check full "env('guacamole')['OPENID_AUTHORIZATION_ENDPOINT'] == 'https://login.example.com/realms/x/protocol/openid-connect/auth'"
check full "env('guacamole')['OPENID_JWKS_ENDPOINT'] == 'https://login.example.com/realms/x/protocol/openid-connect/certs'"
check full "env('guacamole')['EXTENSION_PRIORITY'] == '*, openid'"
check full "obj('Route')['spec']['host'] == 'desk.example.com' and obj('Route')['metadata']['annotations']['cert-manager.io/issuer-kind'] == 'ClusterIssuer'"
for p in "('disable-copy', 'true')" "('disable-paste', 'true')" "('recording-include-keys', 'false')" "('color-depth', '24')" "('hostname', 'kiosk')" "('port', '5900')" "('recording-path', '\${HISTORY_PATH}/\${HISTORY_UUID}')" "('create-recording-path', 'true')" "('password', :'secret_0_0')"; do
  check "full parameter $p" "\"$p\" in sql()"
done
check full "\"\\\\set secret_0_0 \`cat /secrets/vncPassword\`\" in sql()"
check full "\"'guest@example.com'\" in sql() and \"'o''brien@example.com'\" in sql()"
check full "\"'READ'\" in sql() and 'DELETE FROM guacamole_system_permission' in sql()"
check full "\"connection_name = 'desk'\" in sql()"
check full "all(d['kind'] in $NAMESPACED for d in docs)"
hasnt full "kind: Namespace"

# an explicit endpoint wins over the Keycloak default
OUT=$(render $FULL --set oidc.authorizationEndpoint=https://idp.example.com/auth --set oidc.jwksEndpoint=https://idp.example.com/jwks --set oidc.autoRedirect=true)
check endpoints "env('guacamole')['OPENID_AUTHORIZATION_ENDPOINT'] == 'https://idp.example.com/auth'"
check endpoints "env('guacamole')['OPENID_JWKS_ENDPOINT'] == 'https://idp.example.com/jwks'"
check endpoints "env('guacamole')['EXTENSION_PRIORITY'] == 'openid'"

OUT=$(render --set recording.enabled=false --set-json "$CONN")
check norec "[c['name'] for c in pod()['containers']] == ['guacamole', 'guacd']"
check norec "'RECORDING_ENABLED' not in env('guacamole') and not objs('PersistentVolumeClaim')"
check norec "'recording-path' not in sql()"

OUT=$(render --set oidc.enabled=true --set oidc.clientId=desk --set route.hostname=desk.example.com)
has "oidc without issuer" "oidc.issuerUrl is required"
OUT=$(render --set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set route.hostname=desk.example.com)
has "oidc without client" "oidc.clientId is required"
OUT=$(render --set oidc.enabled=true --set oidc.issuerUrl=https://login.example.com/realms/x --set oidc.clientId=desk)
has "oidc without hostname" "route.hostname is required"
OUT=$(render --set recording.s3.enabled=true)
has "s3 without class" "recording.s3.storageClassName is required"

OUT=$(render --set secrets.adminPassword=adm1n --set secrets.vncPassword=vncpw)
check "given secrets" "obj('Secret', 't-guacamole')['data'] == {'adminPassword': 'YWRtMW4=', 'vncPassword': 'dm5jcHc='}"
hasnt "given secrets" "adm1n"

OUT=$(render --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test)
check external "not [d for d in objs('Secret') if d['metadata']['name'] == 't-guacamole']"
check external "obj('ExternalSecret', 't-guacamole')['spec']['target']['name'] == 't-guacamole'"
check external "sorted(x['secretKey'] for x in obj('ExternalSecret')['spec']['data']) == ['adminPassword', 'vncPassword']"
check external "sorted(x['remoteRef']['key'] for x in obj('ExternalSecret')['spec']['data']) == ['guacamole-admin-password', 'guacamole-vnc-password']"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
