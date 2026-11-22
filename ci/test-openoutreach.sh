#!/bin/bash
# Render assertions for charts/openoutreach: suspended and read-only by default, pacing
# bounds enforced by the schema, the test run never sends, and every credential source
set -u
cd "$(dirname "$0")/.."
C=charts/openoutreach
fail=0
render() { helm template oo "$C" --namespace test "$@" 2>&1; }
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
def pod(): return obj("Deployment", "oo")["spec"]["template"]["spec"]
def ctr(): return pod()["containers"][0]
def env(): return {e["name"]: e.get("value") for e in ctr().get("env", [])}
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}
# refuse <label> <helm args...>: the chart must not render
refuse() {
  local label=$1; shift
  if render "$@" >/dev/null; then echo "FAIL $label: rendered with $*"; fail=1; fi
}

OUT=$(render)
check default "obj('Deployment')['spec']['replicas'] == 0 and obj('Deployment')['spec']['strategy'] == {'type': 'Recreate'}"
check default "env()['READ_ONLY'] == 'true' and env()['SEND_ENABLED'] == 'false' and env()['SCOUT_TEST_RUN'] == 'false'"
check default "obj('Deployment')['spec']['selector'] == {'matchLabels': {'app.kubernetes.io/name': 'openoutreach', 'app.kubernetes.io/instance': 'oo'}}"
check default "ctr()['image'].startswith('ghcr.io/hoolia/openoutreach-scout:') and '@sha256:' in ctr()['image']"
check default "'envFrom' not in ctr() and not objs('Secret') and not objs('ExternalSecret') and not objs('Service')"
check default "'imagePullSecrets' not in pod() and pod()['automountServiceAccountToken'] is False and ctr()['readinessProbe']['exec']['command'] == ['oo-scout', 'status']"
check default "obj('PersistentVolumeClaim', 'oo-data')['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'"
check default "env()['SCOUT_TIMEZONE'] == 'Europe/Amsterdam' and env()['CLICKHOUSE_URL'] == ''"
check default "{v['name']: v for v in pod()['volumes']}['linkedin-session'].get('emptyDir') is not None"

OUT=$(render --set suspend=false)
check running "obj('Deployment')['spec']['replicas'] == 1"

# the schema refuses values beyond the pacing bounds
refuse pacing --set pacing.maxPageViewsDay=51
refuse pacing --set pacing.maxConnectsWeek=81
refuse pacing --set pacing.pauseMinMin=29
refuse pacing --set pacing.windowEnd=18:30
refuse pacing --set pacing.windowStart=08:00
refuse pacing --set pacing.delayMinS=10

# a test run has its own caps (at most 5 page views, 10 minutes) and never sends
OUT=$(render --set testRun.enabled=true)
check testRun "env()['SCOUT_MAX_PAGE_VIEWS_DAY'] == '5' and env()['SCOUT_SESSION_MAX_MIN'] == '10' and env()['SCOUT_TEST_RUN'] == 'true'"
refuse testRun --set testRun.maxPageViews=6
refuse testRun --set testRun.enabled=true --set readOnly=false --set sendEnabled=true \
  --set chatwoot.excludedSenderIds=1 --set secrets.existingSecret=creds

# sending needs the excluded sender list and credentials
refuse send --set readOnly=false --set sendEnabled=true --set secrets.existingSecret=creds
refuse send --set readOnly=false --set sendEnabled=true --set chatwoot.excludedSenderIds=1
OUT=$(render --set readOnly=false --set sendEnabled=true --set chatwoot.excludedSenderIds=7 --set secrets.existingSecret=creds)
check send "env()['READ_ONLY'] == 'false' and ctr()['envFrom'] == [{'secretRef': {'name': 'creds'}}]"
# sendEnabled alone stays read-only and needs nothing
OUT=$(render --set sendEnabled=true)
check sendSwitchOnly "env()['READ_ONLY'] == 'true' and 'envFrom' not in ctr()"

# credentials: an existing Secret, values, or a secret store
OUT=$(render --set secrets.existingSecret=creds)
check existingSecret "ctr()['envFrom'] == [{'secretRef': {'name': 'creds'}}] and not objs('Secret')"
OUT=$(render --set secrets.clickhouseUser=writer --set secrets.clickhousePassword=pw)
check kubernetes "sorted(obj('Secret', 'oo-credentials')['data']) == ['CLICKHOUSE_PASSWORD', 'CLICKHOUSE_USER'] and ctr()['envFrom'] == [{'secretRef': {'name': 'oo-credentials'}}]"
check kubernetes "'checksum/credentials' in obj('Deployment')['spec']['template']['metadata']['annotations']"
OUT=$(render --set secrets.type=externalSecret --set secrets.externalSecret.storeName=store \
  --set secrets.externalSecret.items.CLICKHOUSE_PASSWORD=ch-writer \
  --set secrets.externalSecret.items.CLICKHOUSE_USER.key=ch-writer \
  --set secrets.externalSecret.items.CLICKHOUSE_USER.property=login.username)
check externalSecret "{d['secretKey']: (d['remoteRef']['key'], d['remoteRef']['property']) for d in obj('ExternalSecret', 'oo-credentials')['spec']['data']} == {'CLICKHOUSE_PASSWORD': ('ch-writer', 'login.password'), 'CLICKHOUSE_USER': ('ch-writer', 'login.username')}"
check externalSecret "ctr()['envFrom'] == [{'secretRef': {'name': 'oo-credentials'}}] and not objs('Secret')"
refuse externalSecret --set secrets.type=externalSecret --set secrets.externalSecret.items.CLICKHOUSE_PASSWORD=x
refuse externalSecret --set secrets.type=externalSecret --set secrets.externalSecret.storeName=s \
  --set secrets.externalSecret.items.OTHER=x

# a pull Secret for a private mirror
OUT=$(render --set 'imagePullSecrets[0]=mirror')
check imagePullSecrets "pod()['imagePullSecrets'] == [{'name': 'mirror'}]"

# a login seed Secret is mounted, optional
OUT=$(render --set linkedinSession.existingSecret=seed)
check linkedinSession "{v['name']: v for v in pod()['volumes']}['linkedin-session']['secret'] == {'secretName': 'seed', 'optional': True}"

# the playbook reaches the ConfigMap and restarts the pod through its checksum
OUT=$(render --set-json 'playbook={"linkedin":{"rules":[{"id":"r1","people_keywords":["platform engineer"]}]}}')
check playbook "yaml.safe_load(obj('ConfigMap', 'oo-playbook')['data']['playbook.yaml'])['linkedin']['rules'][0]['id'] == 'r1'"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
