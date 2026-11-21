#!/bin/bash
# Render assertions for charts/searxng, charts/mcp-gsc and charts/mcp-higgsfield
set -u
cd "$(dirname "$0")/.."
fail=0
render() { helm template t "charts/$1" --namespace test --api-versions route.openshift.io/v1 "${@:2}" 2>&1; }
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
def ctr(deploy, name):
    for c in obj("Deployment", deploy)["spec"]["template"]["spec"]["containers"]:
        if c["name"] == name: return c
    return {}
def env(deploy, name, var):
    return [e.get("value", e.get("valueFrom")) for e in ctr(deploy, name)["env"] if e["name"] == var]
sys.exit(0 if eval(sys.argv[1]) else 1)
' "$2" || { echo "FAIL $1: $2"; fail=1; }
}

# searxng: names follow the release, the tools find SearXNG and the gateway finds the tools
OUT=$(render searxng)
check searxng "obj('Deployment', 't') and obj('Deployment', 't-mcp') and not objs('Route')"
check searxng "obj('Service', 't-mcp')['spec']['ports'][0]['appProtocol'] == 'agentgateway.dev/mcp'"
check searxng "obj('Service', 't-mcp')['metadata']['annotations']['agentgateway.dev/mcp-target-name'] == 't'"
check searxng "env('t-mcp', 'mcp', 'SEARXNG_URL') == ['http://t:8888']"
check searxng "'t-mcp:8000' in env('t-mcp', 'mcp', 'MCP_HTTP_ALLOWED_HOSTS')[0]"
check searxng "env('t', 'searxng', 'SEARXNG_SECRET') == [{'secretKeyRef': {'name': 't', 'key': 'SEARXNG_SECRET'}}]"
check searxng "'limiter: false' in obj('ConfigMap', 't')['data']['settings.yml']"
check searxng "obj('NetworkPolicy', 't')['spec']['ingress'][0]['from'][0]['podSelector']['matchLabels']['app.kubernetes.io/name'] == 'searxng-mcp'"
OUT=$(render searxng --set secrets.secretKey=k --set route.enabled=true --set mcp.allowedHosts[0]=gw.example.org --set mcp.targetName=web)
check searxng-values "obj('Secret', 't')['data']['SEARXNG_SECRET'] == 'aw=='"
check searxng-values "obj('Route', 't')['spec']['path'] == '/'"
check searxng-values "obj('Route', 't')['metadata']['annotations']['cert-manager.io/issuer-name'] == 'letsencrypt'"
check searxng-values "env('t-mcp', 'mcp', 'MCP_HTTP_ALLOWED_HOSTS')[0].endswith(',gw.example.org')"
check searxng-values "obj('Service', 't-mcp')['metadata']['annotations']['agentgateway.dev/mcp-target-name'] == 'web'"
OUT=$(render searxng --set mcp.enabled=false --set secrets.existingSecret=mine)
check searxng-nomcp "not obj('Deployment', 't-mcp') and not objs('Secret')"

# mcp-gsc: SSE behind its own backend; nothing reaches agents without a key source
OUT=$(render mcp-gsc)
check gsc-default "obj('Deployment', 'gsc') and not objs('AgentgatewayBackend') and not objs('Secret')"
check gsc-default "obj('Deployment', 'gsc')['metadata']['labels']['mcp.openshift.eu/bundle'] == 'gsc'"
check gsc-default "'appProtocol' not in obj('Service', 'gsc')['spec']['ports'][0]"
check gsc-default "ctr('gsc', 'server')['command'] == ['uvx', '--from', 'mcp-search-console==0.4.2', 'mcp-gsc']"
check gsc-default "env('gsc', 'server', 'GSC_ALLOW_DESTRUCTIVE') == ['false'] and env('gsc', 'server', 'MCP_TRANSPORT') == ['sse']"
check gsc-default "obj('Deployment', 'gsc')['spec']['template']['spec']['volumes'][1]['secret']['optional'] is True"
OUT=$(render mcp-gsc --set secrets.type=externalSecret --set secrets.externalSecret.storeName=s --set secrets.externalSecret.item=gsc-key --set agentgatewayBackend.labels.x=y)
check gsc-external "obj('ExternalSecret', 'gsc-credentials')['spec']['data'][0]['remoteRef']['key'] == 'gsc-key'"
check gsc-external "obj('AgentgatewayBackend', 'gsc')['spec']['mcp']['targets'][0]['static'] == {'backendRef': {'name': 'gsc'}, 'port': 8000, 'path': '/sse', 'protocol': 'SSE'}"
check gsc-external "obj('AgentgatewayBackend', 'gsc')['metadata']['labels']['x'] == 'y' and obj('AgentgatewayBackend')['metadata']['labels']['mcp.openshift.eu/server'] == 'gsc'"
OUT=$(render mcp-gsc --set secrets.existingSecret=k --set secrets.existingSecretKey=key.json --set remoteMCPServer.enabled=true --set remoteMCPServer.url=https://gw.example.org/mcp/test/gsc)
check gsc-remote "obj('RemoteMCPServer', 'gsc')['spec']['url'] == 'https://gw.example.org/mcp/test/gsc' and not objs('ExternalSecret')"
check gsc-remote "obj('Deployment', 'gsc')['spec']['template']['spec']['volumes'][1]['secret']['items'][0]['key'] == 'key.json'"

# mcp-higgsfield: kagent MCPServer plus its backend; the key only when one is set
OUT=$(render mcp-higgsfield)
check hf-default "obj('MCPServer', 'higgsfield')['spec']['deployment']['args'] == ['-y', 'higgsfield-mcp@0.2.0']"
check hf-default "'secretRefs' not in obj('MCPServer')['spec']['deployment'] and not objs('Secret')"
check hf-default "obj('AgentgatewayBackend', 'higgsfield')['spec']['mcp']['targets'][0]['static']['backendRef'] == {'name': 'higgsfield'}"
check hf-default "not objs('RemoteMCPServer')"
OUT=$(render mcp-higgsfield --set secrets.type=externalSecret --set secrets.externalSecret.storeName=s --set remoteMCPServer.enabled=true)
check hf-external "obj('MCPServer')['spec']['deployment']['secretRefs'] == [{'name': 'higgsfield-api-key'}]"
check hf-external "sorted(d['secretKey'] for d in obj('ExternalSecret', 'higgsfield-api-key')['spec']['data']) == ['HF_API_KEY', 'HF_SECRET']"
check hf-external "obj('RemoteMCPServer')['spec']['url'] == 'http://higgsfield.test:3000/mcp'"

[ $fail = 0 ] && echo "PASS searxng mcp-gsc mcp-higgsfield" || exit 1
