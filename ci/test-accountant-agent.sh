#!/bin/bash
# Render assertions for charts/accountant-agent, with the API versions of an OpenShift
# cluster that runs kagent
set -u
cd "$(dirname "$0")/.."
C=charts/accountant-agent
fail=0
API=(--api-versions security.openshift.io/v1 --api-versions route.openshift.io/v1)
KAGENT=(--api-versions kagent.dev/v1alpha2/Agent)
MCP=(--set model.baseUrl=https://llm.example.com/v1 --set 'mcpServers[0].name=ledger'
  --set 'mcpServers[0].url=http://ledger-mcp:8080/mcp' --set 'mcpServers[0].tokenAudience=ledger-mcp')
render() { helm template t "$C" --namespace test "$@" 2>&1; }
# check <label> <python expression>; obj(kind, name), objs(kind), cfg() the agent config,
# ctr() the agent container, env() its env
check() {
  echo "$OUT" | python3 -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def names(kind): return sorted(d["metadata"]["name"] for d in objs(kind))
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def cfg(): return json.loads(obj("ConfigMap", "t")["data"]["config.json"])
def ctr(): return obj("Deployment", "t")["spec"]["template"]["spec"]["containers"][0]
def env(): return {e["name"]: e.get("value", e.get("valueFrom")) for e in ctr().get("env", [])}
def sources(): return [s["serviceAccountToken"] for v in obj("Deployment", "t")["spec"]["template"]["spec"]["volumes"] if v["name"] == "tokens" for s in v["projected"]["sources"]]
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}

# default: renders, no cluster-scoped kind, no kagent Agent without the kagent API
OUT=$(render "${API[@]}") || { echo "FAIL default render: $OUT"; fail=1; }
check default 'names("Agent") == []'
check default 'names("CronJob") == ["t-bookkeeper", "t-cfo"]'
check default 'obj("Cluster", "t-ledger")["spec"]["bootstrap"]["initdb"]["postInitApplicationSQLRefs"]["configMapRefs"][0]["name"] == "t-ledger-schema"'
check default 'all(k not in {"Namespace","ClusterRole","ClusterRoleBinding","CustomResourceDefinition","SecurityContextConstraints"} for k in (d["kind"] for d in docs))'
check default 'all(d["metadata"]["namespace"] == "test" for d in docs)'
check default 'env()["TASK_STORE_URL"] == {"secretKeyRef": {"name": "t-ledger-app", "key": "uri"}}'
check default 'cfg()["model"]["baseUrl"] == "" and "tokenFile" not in cfg()["model"]'
check default '"tokens" not in [v["name"] for v in obj("Deployment", "t")["spec"]["template"]["spec"]["volumes"]]'
check default 'cfg()["approval"] == {"approveCommand": "boek", "rejectCommand": "afkeuren"}'

# with kagent and an MCP server: an Agent per role relaying to its port, tokens per audience
OUT=$(render "${API[@]}" "${KAGENT[@]}" "${MCP[@]}") || { echo "FAIL kagent render: $OUT"; fail=1; }
check kagent 'names("Agent") == ["t-bookkeeper", "t-cfo"]'
check kagent 'obj("Agent", "t-cfo")["spec"]["type"] == "BYO"'
check kagent 'obj("Agent", "t-cfo")["spec"]["byo"]["deployment"]["env"] == [{"name": "UPSTREAM", "value": "http://t.test.svc:8082"}]'
check kagent 'obj("Agent", "t-bookkeeper")["spec"]["byo"]["deployment"]["args"] == ["-m", "accountant_agent.relay"]'
check kagent '[p["port"] for p in obj("Service", "t")["spec"]["ports"]] == [8081, 8082]'
check kagent 'sources() == [{"audience": "llm.example.com", "expirationSeconds": 3600, "path": "model/token"}, {"audience": "ledger-mcp", "expirationSeconds": 3600, "path": "mcp-ledger/token"}]'
check kagent 'cfg()["mcpServers"] == [{"name": "ledger", "url": "http://ledger-mcp:8080/mcp", "tokenFile": "/var/run/secrets/tokens/mcp-ledger/token"}]'
check kagent 'cfg()["model"]["tokenFile"] == "/var/run/secrets/tokens/model/token"'
check kagent 'cfg()["publicUrl"] == "http://t.test.svc:{port}/"'
check kagent 'obj("CronJob", "t-cfo")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][3] == "http://t.test.svc:8082/"'

# kagent switched off, one role, an API key, memory task store, no ledger
OUT=$(render "${API[@]}" "${KAGENT[@]}" --set kagent.enabled=false --set roles.cfo.enabled=false \
  --set model.baseUrl=https://llm.example.com/v1 --set model.apiKeySecret.name=llm-key \
  --set taskStore.type=memory --set ledger.enabled=false --set cronJob.timeZone=Europe/Amsterdam) ||
  { echo "FAIL minimal render: $OUT"; fail=1; }
check minimal 'names("Agent") == [] and names("Cluster") == [] and names("CronJob") == ["t-bookkeeper"]'
check minimal 'list(cfg()["roles"]) == ["bookkeeper"]'
check minimal 'cfg()["model"]["apiKeyEnv"] == "MODEL_API_KEY" and "tokenFile" not in cfg()["model"]'
check minimal 'env()["MODEL_API_KEY"] == {"secretKeyRef": {"name": "llm-key", "key": "api-key"}} and "TASK_STORE_URL" not in env()'
check minimal 'obj("CronJob")["spec"]["timeZone"] == "Europe/Amsterdam"'
check minimal 'names("NetworkPolicy") == ["t"]'

# a database task store without a database is refused
OUT=$(render "${API[@]}" --set ledger.enabled=false)
echo "$OUT" | grep -q 'taskStore.type database needs' || { echo "FAIL no-ledger: not refused"; fail=1; }
# an external database Secret replaces the ledger Cluster
OUT=$(render "${API[@]}" --set ledger.enabled=false --set ledger.urlSecret.name=books-db) || { echo "FAIL external db: $OUT"; fail=1; }
check external-db 'env()["TASK_STORE_URL"]["secretKeyRef"]["name"] == "books-db"'
# no role is refused
OUT=$(render "${API[@]}" --set roles.cfo.enabled=false --set roles.bookkeeper.enabled=false)
echo "$OUT" | grep -q 'enable at least one role' || { echo "FAIL no role: not refused"; fail=1; }

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
