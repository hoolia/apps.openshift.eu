#!/bin/bash
# Render assertions for charts/opencode-agent
set -u
cd "$(dirname "$0")/.."
C=charts/opencode-agent
fail=0
BASE=(--set person.name=sam --set person.email=sam@example.com --set-string "person.role=I am sales" --set route.host=sam.example.com)
render() { helm template t "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }
# check <label> <python expression>; obj(kind, name) returns one rendered object,
# byo() the Agent deployment, ctr(name) one container of it, env(name) its env
check() {
  echo "$OUT" | python3 -c '
import base64, json, sys, tomllib, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name=None):
    for d in objs(kind):
        if name is None or d["metadata"]["name"] == name: return d
    return {}
def byo(): return obj("Agent")["spec"]["byo"]["deployment"]
def ctr(name):
    if name == "opencode-a2a": return byo()
    for c in byo().get("extraContainers", []):
        if c["name"] == name: return c
    return {}
def env(name): return {e["name"]: e.get("value") for e in ctr(name).get("env", [])}
def vol(name):
    for v in byo().get("volumes", []):
        if v["name"] == name: return v
    return {}
def cfg(): return obj("ConfigMap", "sam-agent-opencode")["data"]
def proxy(): return obj("ConfigMap", "sam-agent-oauth2-proxy")["data"]
def alpha(): return yaml.safe_load(proxy()["alpha-config.yaml"])
def secret(name, key): return base64.b64decode(obj("Secret", name)["data"][key]).decode()
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}
# run_init <label> <shell snippet>: runs the rendered init.sh in a temp dir
INIT_OUT=""
init_script() { echo "$OUT" | python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if isinstance(d, dict) and d.get("kind") == "ConfigMap" and d["metadata"]["name"].endswith("-opencode"):
        print(d["data"]["init.sh"], end="")'; }

# default: a working agent, only namespaced objects of the release namespace
OUT=$(render "${BASE[@]}")
check default 'obj("Agent")["kind"] == "Agent"'
for k in Agent PersistentVolumeClaim ConfigMap Service Route NetworkPolicy RoleBinding Secret BuildConfig ImageStream; do has default "kind: $k"; done
for k in ClusterPolicy HTTPRoute AgentgatewayPolicy AgentgatewayBackend KeycloakClient ExternalSecret ReferenceGrant Namespace ClusterRoleBinding; do hasnt default "kind: $k"; done
check default 'obj("PersistentVolumeClaim", "sam-agent-data")["spec"]["accessModes"] == ["ReadWriteMany"]'
has default "claimName: sam-agent-data"
check default 'alpha()["providers"][0]["code_challenge_method"] == "S256"'
hasnt default "clientSecretFile"
hasnt default "storageClassName"
check default 'obj("PersistentVolumeClaim", "sam-agent-data")["spec"]["resources"]["requests"]["storage"] == "20Gi"'
check default 'obj("PersistentVolumeClaim", "sam-agent-data")["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Delete=false,Prune=false"'
check "default namespace" 'all(d["metadata"].get("namespace", "test") == "test" for d in docs)'
check "owner email only" 'proxy()["emails.txt"].split() == ["sam@example.com"]'
check "flock" '"flock -x /workspace/.opencode.lock" in " ".join(ctr("opencode")["command"])'
check "flock wraps opencode" '"flock -x /workspace/.opencode.lock opencode web" in " ".join(ctr("opencode")["command"])'
# the adapter answers A2A while another pod still holds the lock
check "adapter readiness" '"readinessProbe" not in byo() or "4096" not in json.dumps(byo()["readinessProbe"])'
check "generated secrets" 'len(secret("sam-agent-server-password", "password")) == 48 and len(secret("sam-agent-a2a-auth", "token")) == 48 and len(secret("sam-agent-cookie-secret", "cookie-secret")) == 32'
check "a2a credentials" 'json.loads(secret("sam-agent-a2a-auth", "credentials"))[0]["token"] == secret("sam-agent-a2a-auth", "token")'
check "basic header" 'secret("sam-agent-server-password", "basic-auth-header") == "Basic " + base64.b64encode(("opencode:" + secret("sam-agent-server-password", "password")).encode()).decode()'
check "no oidc secret" 'not obj("Secret", "sam-agent-oidc") and not vol("oidc")'
check "a2a secret ref" 'any(e.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == "sam-agent-a2a-auth" for e in byo()["env"])'
check "projected token" 'vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]["expirationSeconds"] == 3600 and vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]["path"] == "token"'
check "projected audience" '"audience" not in vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]'
check "token proxy env" 'env("token-proxy")["TOKEN_FILE"] == "/var/run/secrets/api-token/token" and not {"TOKEN_URL", "CLIENT_ID", "CLIENT_SECRET_FILE"} & set(env("token-proxy"))'
check "token proxy no oidc mount" 'all(m["name"] != "oidc" for m in ctr("token-proxy")["volumeMounts"])'
check "token mount" 'any(m["mountPath"] == "/var/run/secrets/api-token" and m["name"] == "api-token" for m in ctr("token-proxy")["volumeMounts"])'
check "chart labels" 'all(d["metadata"]["labels"]["app.kubernetes.io/name"] == "opencode-agent" for d in docs)'

# ported: pins, BuildConfig names, portable labels, ports, role line, MCP, npmrc
check "pins opencode" '"FROM ghcr.io/anomalyco/opencode:1.19.3" in [b for b in objs("BuildConfig") if "a2a" not in b["metadata"]["name"]][0]["spec"]["source"]["dockerfile"]'
check "pins mcp" '"quay.io/containers/kubernetes_mcp_server:v0.0.67" in [b for b in objs("BuildConfig") if "a2a" not in b["metadata"]["name"]][0]["spec"]["source"]["dockerfile"]'
check "pins git" '"apk add --no-cache git openssh-client" in [b for b in objs("BuildConfig") if "a2a" not in b["metadata"]["name"]][0]["spec"]["source"]["dockerfile"]'
check "pins a2a" '"opencode-a2a==1.3.6" in [b for b in objs("BuildConfig") if "a2a" in b["metadata"]["name"]][0]["spec"]["source"]["dockerfile"]'
check "pins a2a base" '"FROM registry.access.redhat.com/ubi10/python-312-minimal" in [b for b in objs("BuildConfig") if "a2a" in b["metadata"]["name"]][0]["spec"]["source"]["dockerfile"]'
check "pins oauth2-proxy" 'ctr("oauth2-proxy")["image"] == "quay.io/oauth2-proxy/oauth2-proxy:v7.7.1"'
check "buildconfig names" 'sorted(b["metadata"]["name"].rsplit("-", 1)[0] for b in objs("BuildConfig")) == ["sam-agent-opencode", "sam-agent-opencode-a2a"]'
check "buildconfig hash" 'all(b["metadata"]["name"].endswith("-" + b["spec"]["output"]["to"]["name"].rsplit("-", 1)[1]) for b in objs("BuildConfig"))'
check "buildconfig trigger" 'all(b["spec"]["triggers"] == [{"type": "ConfigChange"}] for b in objs("BuildConfig"))'
check "pod pulls built tag" 'ctr("opencode")["image"].endswith("/test/" + [b for b in objs("BuildConfig") if "a2a" not in b["metadata"]["name"]][0]["spec"]["output"]["to"]["name"]) and byo()["image"].endswith("/test/" + [b for b in objs("BuildConfig") if "a2a" in b["metadata"]["name"]][0]["spec"]["output"]["to"]["name"])'
check "portable labels" 'byo()["labels"] == {"kagenti.io/type": "agent", "protocol.kagenti.io/a2a": "", "app.kubernetes.io/name": "sam-agent", "app.kubernetes.io/part-of": "test"}'
check "ports TCP" 'all(p.get("protocol") == "TCP" for c in [byo()] + byo()["extraContainers"] for p in c.get("ports", []))'
check "containers" 'sorted(c["name"] for c in byo()["extraContainers"]) == ["oauth2-proxy", "opencode", "token-proxy"]'
check "adapter env" 'env("opencode-a2a")["A2A_PORT"] == "8080" and env("opencode-a2a")["A2A_CLIENT_ALLOWED_HOSTS"] == "127.0.0.1" and byo()["replicas"] == 1'
check "role line" '"- I am sales" in cfg()["instructions.md"] and "/etc/opencode-agent/instructions.md" in json.loads(cfg()["opencode.json"])["instructions"]'
check "agent description" 'obj("Agent")["spec"]["description"] == "OpenCode agent of sam. I am sales"'
check "local stdio mcp" 'json.loads(cfg()["opencode.json"])["mcp"]["kubernetes"]["type"] == "local" and "--disable-multi-cluster" in json.loads(cfg()["opencode.json"])["mcp"]["kubernetes"]["command"]'
check "mcp denies secrets" '{"group": "", "version": "v1", "kind": "Secret"} in tomllib.loads(cfg()["kubernetes-mcp-server.toml"])["denied_resources"]'
check "npmrc empty" 'cfg()["npmrc"].strip() == ""'
check "serviceaccount" 'byo()["serviceAccountName"] == "sam-agent" and not objs("ServiceAccount") and obj("RoleBinding")["subjects"] == [{"kind": "ServiceAccount", "name": "sam-agent", "namespace": "test"}]'
check "rolebinding edit" 'obj("RoleBinding")["roleRef"]["name"] == "edit"'
check "route timeout" 'obj("Route", "sam-agent")["metadata"]["annotations"]["haproxy.router.openshift.io/timeout"] == "3600s" and obj("Route")["spec"]["host"] == "sam.example.com" and obj("Route")["spec"]["to"]["name"] == "sam-agent-ui"'
check "route cert-manager" 'obj("Route")["metadata"]["annotations"]["cert-manager.io/issuer-kind"] == "ClusterIssuer" and obj("Route")["metadata"]["annotations"]["cert-manager.io/issuer-name"] == "letsencrypt"'
check "network policy" 'sorted(r["ports"][0]["port"] for r in obj("NetworkPolicy")["spec"]["ingress"]) == [4180]'

OUT=$(render "${BASE[@]}" --set-json 'opencode.npmScopes={"@example":"https://npm.example.com/repo/"}')
check npmrc 'cfg()["npmrc"].strip() == "@example:registry=https://npm.example.com/repo/"'

# RollingUpdate with a single-attach volume stalls every rollout
for m in ReadWriteOnce ReadWriteOncePod; do
  OUT=$(render "${BASE[@]}" --set storage.accessMode=$m)
  has "access mode $m rejected" "accessMode"
  hasnt "access mode $m rejected" "kind: PersistentVolumeClaim"
done

# no values at all: the gate renders this, the release name names the agent
OUT=$(render)
check "no values agent" 'obj("Agent")["kind"] == "Agent"'
has "no values" "claimName: t-agent-data"
check "no values route" '"host" not in obj("Route")["spec"] and "cert-manager.io/issuer-kind" not in obj("Route")["metadata"].get("annotations", {})'

OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com)
check "upstream audience" 'vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "api.example.com"'
check upstream 'env("token-proxy")["UPSTREAM"] == "https://api.example.com"'
OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com:8443/v1 --set gateway.audience=other)
check "audience override" 'vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "other"'
OUT=$(render "${BASE[@]}" --set gateway.upstream=https://api.example.com:8443/v1)
check "audience port" 'vol("api-token")["projected"]["sources"][0]["serviceAccountToken"]["audience"] == "api.example.com"'

OUT=$(render "${BASE[@]}" --set oauth.issuerUrl=https://login.example.com/realms/x --set oauth.clientId=c1 --set 'oauth.allowedGroups={team-admins}' --set oauth.groupsClaim=roles)
check "allowed groups" 'alpha()["providers"][0]["allowedGroups"] == ["team-admins"]'
check "groups claim" 'alpha()["providers"][0]["oidcConfig"]["groupsClaim"] == "roles"'
check "issuer" 'alpha()["providers"][0]["oidcConfig"]["issuerURL"] == "https://login.example.com/realms/x" and alpha()["providers"][0]["clientID"] == "c1"'
check "scope" 'alpha()["providers"][0]["scope"] == "openid email profile"'

OUT=$(render "${BASE[@]}" --set secrets.clientSecret=s3cr3t --set secrets.serverPassword=pw)
has confidential "clientSecretFile"
hasnt confidential "s3cr3t"
check confidential 'secret("sam-agent-oidc", "clientSecret") == "s3cr3t" and vol("oidc")["secret"]["secretName"] == "sam-agent-oidc"'
check "oidc mounted for oauth2-proxy only" '[c["name"] for c in byo()["extraContainers"] if any(m["name"] == "oidc" for m in c.get("volumeMounts", []))] == ["oauth2-proxy"]'
check "server password value" 'secret("sam-agent-server-password", "basic-auth-header") == "Basic " + base64.b64encode(b"opencode:pw").decode()'

# a cookie secret of another length is hashed to 32 for oauth2-proxy
OUT=$(render "${BASE[@]}" --set secrets.cookieSecret=e9e2334228689ae870e4f029424641eabcf5c9efbc2d3772)
check cookie 'len(secret("sam-agent-cookie-secret", "cookie-secret")) == 32'

OUT=$(render "${BASE[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test --set git.url=https://git.example.com/org/site.git)
has external "kind: ExternalSecret"
hasnt external "kind: Secret"
hasnt external "kind: Password"
check external 'sorted(e["metadata"]["name"] for e in objs("ExternalSecret")) == ["sam-agent-a2a-auth", "sam-agent-cookie-secret", "sam-agent-git-token", "sam-agent-server-password"]'
check "external store" 'all(e["spec"]["secretStoreRef"] == {"kind": "ClusterSecretStore", "name": "vault-test"} for e in objs("ExternalSecret"))'
check "external defaults" 'all((r["remoteRef"]["conversionStrategy"], r["remoteRef"]["decodingStrategy"], r["remoteRef"]["metadataPolicy"], r["remoteRef"]["nullBytePolicy"]) == ("Default", "None", "None", "Ignore") for e in objs("ExternalSecret") for r in e["spec"]["data"])'
check "external a2a keys" 'set(obj("ExternalSecret", "sam-agent-a2a-auth")["spec"]["target"]["template"]["data"]) == {"token", "credentials"}'
check "external public client" 'not obj("ExternalSecret", "sam-agent-oidc")'
OUT=$(render "${BASE[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test --set secrets.externalSecret.items.clientSecret=item-oidc)
has "external confidential" "clientSecretFile"
check "external confidential" 'obj("ExternalSecret", "sam-agent-oidc")["spec"]["data"][0]["remoteRef"]["key"] == "item-oidc"'

OUT=$(render "${BASE[@]}" --set route.labels.x=y)
check "route labels" 'obj("Route")["metadata"]["labels"]["x"] == "y" and [d["kind"] for d in docs if "x" in d["metadata"]["labels"]] == ["Route"]'

OUT=$(render "${BASE[@]}" --set a2a.gatewayNamespace=gw)
check "reference grant" 'obj("ReferenceGrant", "sam-agent-a2a")["spec"]["from"] == [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": "gw"}] and obj("ReferenceGrant")["spec"]["to"] == [{"group": "", "kind": "Service", "name": "sam-agent"}] and obj("ReferenceGrant")["metadata"]["namespace"] == "test"'
check "a2a network policy" '[p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for r in obj("NetworkPolicy")["spec"]["ingress"] if r["ports"][0]["port"] == 8080 for p in r["from"]] == ["gw"]'
OUT=$(render "${BASE[@]}" --set a2a.gatewayNamespace=gw --set 'networkPolicy.a2aNamespaces={gw,kagent}')
check "a2a namespaces" '[p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for r in obj("NetworkPolicy")["spec"]["ingress"] if r["ports"][0]["port"] == 8080 for p in r["from"]] == ["gw", "kagent"]'

OUT=$(render "${BASE[@]}" --set builds.enabled=false --set a2a.image=registry.example.com/opencode-a2a:1.3.6)
check "prebuilt" 'ctr("opencode")["image"] == "ghcr.io/anomalyco/opencode:1.19.3" and not objs("BuildConfig") and not objs("ImageStream")'
OUT=$(render "${BASE[@]}" --set builds.enabled=false)
has "required a2a.image" "a2a.image is required when builds.enabled is false"
OUT=$(render "${BASE[@]}" --set secrets.type=externalSecret)
has "required storeName" "secrets.externalSecret.storeName is required"

OUT=$(render "${BASE[@]}" --set a2a.baseImage=example.com/python)
B1=$(echo "$OUT" | grep -E '^  name: sam-agent-opencode-a2a-[0-9a-f]{8}$')
OUT=$(render "${BASE[@]}")
B2=$(echo "$OUT" | grep -E '^  name: sam-agent-opencode-a2a-[0-9a-f]{8}$')
[ -n "$B1" ] && [ "$B1" != "$B2" ] || { echo "FAIL buildconfig name follows Dockerfile"; fail=1; }

# init.sh: the Git token goes to the clone host only; an existing clone is never touched
OUT=$(render "${BASE[@]}" --set git.url=https://git.example.com/org/site.git)
check "git token mount" 'vol("git-token")["secret"]["secretName"] == "sam-agent-git-token" and env("opencode")["REPO_DIR"] == "/workspace/site"'
check "git token secret" 'obj("Secret", "sam-agent-git-token")["type"] == "Opaque"'
T=$(mktemp -d)
init_script > "$T/init.sh"
mkdir -p "$T/workspace/site/.git" "$T/bin"
printf '#!/bin/sh\necho "$@" >> %s/git-calls\nexit 1\n' "$T" > "$T/bin/git"
chmod +x "$T/bin/git"
env -i PATH="$T/bin:$PATH" HOME="$T/home" REPO_DIR="$T/workspace/site" GIT_URL=https://oauth2@git.example.com/org/site.git \
  GIT_TOKEN_FILE="$T/token" sh "$T/init.sh" >/dev/null 2>&1 || { echo "FAIL init: existing clone"; fail=1; }
[ -e "$T/git-calls" ] && { echo "FAIL init: git called although the clone exists"; fail=1; }
grep -q '\[credential "https://git.example.com"\]' "$T/home/.gitconfig" || { echo "FAIL init: token not offered to the clone host"; fail=1; }
grep -q 'oauth2@' "$T/home/.gitconfig" && { echo "FAIL init: user part in credential host"; fail=1; }
rm -rf "$T/workspace/site"
env -i PATH="$T/bin:$PATH" HOME="$T/home" REPO_DIR="$T/workspace/site" GIT_URL=https://git.example.com/org/site.git \
  sh "$T/init.sh" >/dev/null 2>&1 && { echo "FAIL init: failed clone not reported"; fail=1; }
grep -q clone "$T/git-calls" 2>/dev/null || { echo "FAIL init: no clone without a work tree"; fail=1; }
printf '@example:registry=https://npm.example.com/\n' > "$T/npmrc"
env -i PATH="$PATH" HOME="$T/home2" NPMRC_SOURCE="$T/npmrc" sh "$T/init.sh" >/dev/null 2>&1 || { echo "FAIL init: npmrc run"; fail=1; }
cmp -s "$T/npmrc" "$T/home2/.npmrc" || { echo "FAIL init: npmrc not copied"; fail=1; }
rm -rf "$T"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
