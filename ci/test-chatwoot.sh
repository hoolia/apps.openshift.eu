#!/bin/bash
# Render assertions for charts/chatwoot
set -u
cd "$(dirname "$0")/.."
C=charts/chatwoot
fail=0
API=(--api-versions security.openshift.io/v1 --api-versions route.openshift.io/v1)
SSO=(--set sso.enabled=true --set sso.issuerUrl=https://login.example.com/realms/acme
  --set route.hostname=cw.example.com --set 'sso.allowedGroups={acme-chatwoot}' --set 'sso.adminGroups={acme-admins}')
OBC=(--set attachments.storage=objectBucketClaim --set attachments.objectBucketClaim.storageClassName=bucket-class)
render() { helm template t "$C" --namespace test "${API[@]}" "$@" 2>&1; }
fails() { # fails <label> <message part> <args...>
  local label=$1 msg=$2; shift 2
  out=$(helm template t "$C" --namespace test "${API[@]}" "$@" 2>&1) && { echo "FAIL $label: rendered"; fail=1; return; }
  echo "$out" | grep -qF -- "$msg" || { echo "FAIL $label: wrong error: $out"; fail=1; }
}
# check <label> <python expression> over the rendered objects
check() {
  echo "$OUT" | python3 -c '
import base64, json, re, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
def objs(kind): return [d for d in docs if d["kind"] == kind]
def obj(kind, name):
    for d in objs(kind):
        if d["metadata"]["name"] == name: return d
    return {}
def pod(name):
    o = obj("Deployment", name) or obj("Job", name)
    return o.get("spec", {}).get("template", {}).get("spec", {})
def pods(): return [d["spec"]["template"]["spec"] for d in docs if d["kind"] in ("Deployment", "Job")]
def ctrs(p): return p.get("containers", []) + p.get("initContainers", [])
def ctr(name, c):
    for x in ctrs(pod(name)):
        if x["name"] == c: return x
    return {}
def env(name, c): return {e["name"]: e.get("value") for e in ctr(name, c).get("env", [])}
def refs(p): return json.dumps(p)
def args(): return ctr("chatwoot-gate", "oauth2-proxy")["args"]
def routes():
    out = []
    for a in args():
        if a.startswith("--skip-auth-route="):
            rule = a.split("=", 1)[1]
            method, _, rx = rule.partition("=") if re.match(r"^[A-Z]+=", rule) else ("", "", rule)
            out.append((method, re.compile(rx)))
    return out
def public(method, path): return any((not m or m == method) and rx.search(path) for m, rx in routes())
def secret(name, key): return base64.b64decode(obj("Secret", name)["data"][key]).decode()
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}

# --- default: own login, attachments on a volume, address from the Route
OUT=$(render)
check kinds '{d["kind"] for d in docs} == {"Cluster", "ConfigMap", "Deployment", "Job", "NetworkPolicy", "PersistentVolumeClaim", "Role", "RoleBinding", "Route", "Secret", "Service", "ServiceAccount"}'
check "no gate without sso" 'not obj("Deployment", "chatwoot-gate") and obj("Route", "chatwoot")["spec"]["to"]["name"] == "chatwoot-web"'
check "ce image" 'all(c["image"] == "docker.io/chatwoot/chatwoot:v4.19.1-ce" for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-prepare-1") for c in pod(n)["containers"])'
check "pinned images" 'all(":" in c["image"] and not c["image"].endswith(":latest") for p in pods() for c in ctrs(p))'
check restricted 'all(c["securityContext"] == {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}} for p in pods() for c in ctrs(p))'
check "pod security" 'all(p["securityContext"] == {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}} for p in pods())'
check "no fixed user, host or privilege" 'not re.search(r"runAsUser|hostPath|hostNetwork|privileged|fsGroup", refs([p for p in pods()]))'
check resources 'all(c["resources"]["requests"].get("cpu") and c["resources"]["requests"].get("memory") and c["resources"]["limits"].get("memory") for p in pods() for c in ctrs(p))'
check "requests fit 4 cpu 8Gi" 'sum(float(c["resources"]["requests"]["cpu"].rstrip("m")) for p in pods() for c in p["containers"]) <= 4000 and sum(int(c["resources"]["requests"]["memory"].rstrip("Mi")) for p in pods() for c in p["containers"]) <= 8192'
check "web on the volume" 'obj("Deployment", "chatwoot-web")["spec"]["strategy"] == {"type": "Recreate"} and obj("Deployment", "chatwoot-web")["spec"]["replicas"] == 1 and env("chatwoot-web", "web")["ACTIVE_STORAGE_SERVICE"] == "local"'
check "worker next to web" 'pod("chatwoot-worker")["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["topologyKey"] == "kubernetes.io/hostname"'
check "job holds no volume" '"chatwoot-storage" not in refs(pod("chatwoot-prepare-1")) and "chatwoot-storage" in refs(pod("chatwoot-web"))'
check "volume kept" 'obj("PersistentVolumeClaim", "chatwoot-storage")["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"'
check "route reader" 'obj("Role", "chatwoot-route")["rules"] == [{"apiGroups": ["route.openshift.io"], "resources": ["routes"], "resourceNames": ["chatwoot"], "verbs": ["get"]}] and pod("chatwoot-web")["serviceAccountName"] == "chatwoot-route" and ctr("chatwoot-web", "route-host")'
check "frontend from the route" '"FRONTEND_URL" not in env("chatwoot-web", "web") and ctr("chatwoot-web", "web")["command"][:2] == ["/bin/sh", "/scripts/start.sh"]'
check "break-glass admin under .invalid" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ADMIN_EMAIL"] == "superadmin@chatwoot.invalid"'
check "no platform token without sso" '"chatwoot-platform" not in refs(docs) and "CHATWOOT_PLATFORM_TOKEN" not in env("chatwoot-prepare-1", "prepare")'
check "signup off" 'env("chatwoot-web", "web")["ENABLE_ACCOUNT_SIGNUP"] == "false" and env("chatwoot-web", "web")["ENABLE_PUSH_RELAY_SERVER"] == "false" and env("chatwoot-web", "web")["DISABLE_TELEMETRY"] == "true"'
check "database tls" 'env("chatwoot-web", "web")["PGSSLMODE"] == "verify-full" and env("chatwoot-web", "web")["PGSSLROOTCERT"] == "/db-ca/ca.crt"'
check pgvector 'obj("Cluster", "chatwoot-db")["spec"]["imageName"].endswith("-standard-trixie") and "CREATE EXTENSION IF NOT EXISTS vector" in obj("Cluster", "chatwoot-db")["spec"]["bootstrap"]["initdb"]["postInitApplicationSQL"]'
check "single instance without pdb" 'obj("Cluster", "chatwoot-db")["spec"]["enablePDB"] is False'
check "wait for schema, not health" 'ctr("chatwoot-web", "wait-database")["command"][-1] == "/scripts/wait_ready.rb" and ctr("chatwoot-worker", "wait-database")["command"][-1] == "/scripts/wait_ready.rb"'
check "probes" 'ctr("chatwoot-web", "web")["readinessProbe"]["httpGet"]["path"] == "/health" and ctr("chatwoot-worker", "worker")["readinessProbe"]["exec"]'
# Argo CD: a Sync hook in wave 1. Plain Helm: an ordinary object (no Helm hook), so
# --wait waits for web while the Job already runs; named after the revision
check "job is an argo sync hook and no helm hook" 'obj("Job", "chatwoot-prepare-1")["metadata"]["annotations"] == {"argocd.argoproj.io/hook": "Sync", "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation", "argocd.argoproj.io/sync-wave": "1"} and not any(k.startswith("helm.sh/") for d in docs for k in d["metadata"].get("annotations", {}) if k != "helm.sh/resource-policy")'
check "web after the job" 'obj("Deployment", "chatwoot-web")["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "2" and obj("Deployment", "chatwoot-worker")["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "2"'
check "job waits for the database and pgvector first" 'ctr("chatwoot-prepare-1", "wait-database")["command"][-1] == "/scripts/wait_ready.rb" and env("chatwoot-prepare-1", "wait-database")["CHATWOOT_WAIT_FOR"] == "database" and ctr("chatwoot-prepare-1", "prepare")["command"][-1].index("db:chatwoot_prepare") < ctr("chatwoot-prepare-1", "prepare")["command"][-1].index("bootstrap.rb")'
check "web and worker wait for the bootstrap" 'all("CHATWOOT_WAIT_FOR" not in env(n, "wait-database") and env(n, "wait-database")["CHATWOOT_ACCOUNT_ID"] == "1" for n in ("chatwoot-web", "chatwoot-worker"))'
check "job revision name" 'obj("Job", "chatwoot-prepare-1")["spec"]["template"]["metadata"]["labels"] == {"app.kubernetes.io/name": "chatwoot-prepare"}'
check "no session hours without sso" '"CHATWOOT_SESSION_HOURS" not in env("chatwoot-web", "web")'
check "valkey on a volume with auth" '"--requirepass \"$REDIS_PASSWORD\"" in ctr("chatwoot-redis", "valkey")["args"][0] and "--appendonly yes" in ctr("chatwoot-redis", "valkey")["args"][0] and obj("Deployment", "chatwoot-redis")["spec"]["strategy"] == {"type": "Recreate"}'
check "redis only from chatwoot" 'obj("NetworkPolicy", "chatwoot-redis")["spec"]["ingress"][0]["from"][0]["podSelector"]["matchExpressions"][0]["values"] == ["chatwoot-web", "chatwoot-worker", "chatwoot-prepare"]'
check "web from the router without sso" 'obj("NetworkPolicy", "chatwoot-web")["spec"]["ingress"][0]["from"] == [{"namespaceSelector": {"matchLabels": {"policy-group.network.openshift.io/ingress": ""}}}]'
check "database pods not isolated" 'all("chatwoot-db" not in json.dumps(n["spec"]["podSelector"]) for n in objs("NetworkPolicy"))'
check "generated secrets" 'len(secret("chatwoot-credentials", "secret-key-base")) == 128 and len(secret("chatwoot-credentials", "redis-password")) == 32 and secret("chatwoot-credentials", "super-admin-password").endswith("-Aa1")'
check "no secret in plain env" 'all("value" not in e for p in pods() for c in ctrs(p) for e in c.get("env", []) if re.search("PASSWORD|SECRET|TOKEN", e["name"]) and e["name"] not in ("CHATWOOT_PLATFORM_APP",))'

# --- sso on, attachments in a bucket: the managed Template's shape
OUT=$(render "${SSO[@]}" "${OBC[@]}")
check "route to the gate" 'obj("Route", "chatwoot")["spec"]["to"]["name"] == "chatwoot-gate" and obj("Route", "chatwoot")["spec"]["host"] == "cw.example.com"'
check "no route reader with a hostname" 'not obj("Role", "chatwoot-route") and all(p.get("automountServiceAccountToken") is False for p in pods())'
check "gate containers" '[c["name"] for c in pod("chatwoot-gate")["containers"]] == ["oauth2-proxy", "login-bridge"]'
check "bridge has no port" '"ports" not in ctr("chatwoot-gate", "login-bridge") and env("chatwoot-gate", "login-bridge")["LISTEN_PORT"] == "8081" and obj("Service", "chatwoot-gate")["spec"]["ports"] == [{"name": "http", "port": 4180, "targetPort": "proxy"}]'
check "bridge upstream on loopback" '[a for a in args() if a.startswith("--upstream=")] == ["--upstream=http://chatwoot-web:3000/", "--upstream=http://127.0.0.1:8081/app/login"]'
check "proxy passes no tokens" 'all(a in args() for a in ("--pass-access-token=false", "--pass-authorization-header=false", "--set-xauthrequest=false", "--skip-auth-strip-headers=true", "--pass-user-headers=true", "--cookie-secure=true"))'
check "allowed group" '"--allowed-group=acme-chatwoot" in args() and env("chatwoot-gate", "login-bridge")["ALLOWED_GROUPS"] == "acme-chatwoot" and env("chatwoot-gate", "login-bridge")["ADMIN_GROUPS"] == "acme-admins"'
check "client id from the host" 'not [a for a in args() if a.startswith("--client-id")] and "--config=/run/chatwoot/oauth2-proxy.cfg" in args() and ctr("chatwoot-gate", "route-host")["command"][-1] == "cw.example.com"'
check "platform token only for bridge and job" '{n for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-redis", "chatwoot-gate", "chatwoot-prepare-1") if "chatwoot-platform" in refs(pod(n))} == {"chatwoot-gate", "chatwoot-prepare-1"} and "chatwoot-platform" not in refs(ctr("chatwoot-gate", "oauth2-proxy"))'
check "oauth secret only for oauth2-proxy" '{n for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-redis", "chatwoot-prepare-1") if "chatwoot-oauth" in refs(pod(n))} == set() and "chatwoot-oauth" not in refs(ctr("chatwoot-gate", "login-bridge"))'
check "platform secret file mode" 'pod("chatwoot-gate")["volumes"][2]["secret"] == {"secretName": "chatwoot-platform", "defaultMode": 288}'
check "web only from the gate" 'obj("NetworkPolicy", "chatwoot-web")["spec"]["ingress"][0]["from"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-gate"}}}]'
check "gate only from the router" 'obj("NetworkPolicy", "chatwoot-gate")["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 4180}], "from": [{"namespaceSelector": {"matchLabels": {"policy-group.network.openshift.io/ingress": ""}}}]}]'
# what a website visitor needs, and nothing that logs in or administers
check "widget is public" 'all(public(m, p) for m, p in [("GET", "/packs/js/sdk.js"), ("GET", "/widget"), ("GET", "/vite/assets/widget-B1x9.js"), ("POST", "/api/v1/widget/messages"), ("POST", "/api/v1/widget/config"), ("GET", "/api/v1/widget/messages"), ("GET", "/cable"), ("GET", "/rails/active_storage/blobs/proxy/abc/f.png"), ("GET", "/rails/active_storage/representations/proxy/a/b/f.png"), ("GET", "/audio/widget/ding.mp3"), ("GET", "/assets/images/chatwoot_bot.png"), ("GET", "/vite/assets/Inter-Regular-B0QUfDW0.woff2")])'
check "dashboard, login and admin need a login" 'not any(public(m, p) for m, p in [("GET", "/"), ("GET", "/app"), ("GET", "/app/login"), ("GET", "/app/login/sso"), ("GET", "/app/accounts/1/dashboard"), ("POST", "/auth/sign_in"), ("POST", "/auth/password"), ("GET", "/auth/validate_token"), ("GET", "/super_admin"), ("POST", "/super_admin/sign_in"), ("GET", "/super_admin/platform_apps"), ("GET", "/monitoring/sidekiq"), ("GET", "/installation/onboarding"), ("POST", "/installation/onboarding"), ("GET", "/platform/api/v1/users/1/login"), ("POST", "/platform/api/v1/users"), ("GET", "/api/v1/accounts/1/conversations"), ("GET", "/api/v1/profile"), ("POST", "/rails/active_storage/direct_uploads"), ("PUT", "/rails/active_storage/disk/abc"), ("GET", "/swagger"), ("GET", "/api"), ("GET", "/health"), ("POST", "/cable"), ("GET", "/widgetx"), ("GET", "/widget/../app"), ("GET", "/vite/assets/../../app"), ("GET", "/public/api/v1/inboxes/x/contacts"), ("GET", "/survey/responses/x"), ("GET", "/hc/x"), ("POST", "/webhooks/whatsapp/x"), ("POST", "/rails/action_mailbox/relay/inbound_emails"), ("GET", "/assets/images/../../app"), ("GET", "/audio/widget/../../app")])'
check "obc attachments" 'env("chatwoot-web", "web")["ACTIVE_STORAGE_SERVICE"] == "s3_compatible" and "STORAGE_ENDPOINT" not in env("chatwoot-web", "web") and "AWS_CA_BUNDLE" not in env("chatwoot-web", "web") and env("chatwoot-web", "web")["CHART_S3_EXTRA_CA"] == "/etc/chatwoot/s3-ca/service-ca.crt" and obj("ObjectBucketClaim", "chatwoot-attachments")["spec"]["storageClassName"] == "bucket-class" and not obj("PersistentVolumeClaim", "chatwoot-storage")'
check "rolling web with a bucket" '"strategy" not in obj("Deployment", "chatwoot-web")["spec"] and "affinity" not in pod("chatwoot-worker")'
check "platform app for the bridge" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_PLATFORM_APP"] == "login-bridge" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ADMIN_EMAIL"] == "superadmin@chatwoot.invalid"'
check "bridge refuses the break-glass admin" 'env("chatwoot-gate", "login-bridge")["RESERVED_EMAILS"] == "superadmin@chatwoot.invalid"'
check "groups re-checked at the provider" '"--cookie-refresh=5m" in args() and "--cookie-expire=12h" in args()'
check "chatwoot session hours" 'all(env(n, c)["CHATWOOT_SESSION_HOURS"] == "36" for n, c in (("chatwoot-web", "web"), ("chatwoot-worker", "worker"), ("chatwoot-prepare-1", "prepare")))'
check "secrets before the route" 'all(s["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "-1" for s in objs("Secret")) and "argocd.argoproj.io/sync-wave" not in obj("Route", "chatwoot")["metadata"].get("annotations", {})'
check "sso secrets generated" 'len(secret("chatwoot-platform", "platform-token")) == 48 and len(secret("chatwoot-oauth", "cookie-secret")) == 32 and len(secret("chatwoot-oauth", "client-secret")) == 32'

# --- optional public paths
OUT=$(render "${SSO[@]}" --set sso.public.widget=false --set sso.public.inboxApi=true --set sso.public.surveys=true --set sso.public.helpCenter=true)
check "widget can be closed" 'not public("GET", "/widget") and not public("GET", "/cable") and not public("POST", "/api/v1/widget/messages")'
check "optional paths open" 'public("POST", "/public/api/v1/inboxes/x/contacts") and public("GET", "/survey/responses/x") and public("GET", "/public/api/v1/csat_survey/x") and public("GET", "/hc/docs/en")'
check "optional paths keep login closed" 'not public("GET", "/app/login") and not public("POST", "/auth/sign_in") and not public("GET", "/super_admin")'

# --- deterministic: every secret given, as a Template passes them
GIVEN=(--set secrets.secretKeyBase=k --set secrets.redisPassword=r --set secrets.superAdminPassword=s
  --set secrets.platformToken=p --set secrets.oauthClientSecret=c --set secrets.oauthCookieSecret=cookie0123456789cookie0123456789)
a=$(render "${SSO[@]}" "${OBC[@]}" "${GIVEN[@]}"); b=$(render "${SSO[@]}" "${OBC[@]}" "${GIVEN[@]}")
[ "$a" = "$b" ] || { echo "FAIL deterministic: two renders differ"; fail=1; }
OUT=$a
check "given secrets" 'secret("chatwoot-credentials", "secret-key-base") == "k" and secret("chatwoot-credentials", "super-admin-password") == "s-Aa1" and secret("chatwoot-platform", "platform-token") == "p" and secret("chatwoot-oauth", "client-secret") == "c"'
check "pods restart on a script change" 'all(obj("Deployment", n)["spec"]["template"]["metadata"]["annotations"]["checksum/scripts"] for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-gate"))'
OUT=$(render "${SSO[@]}" "${OBC[@]}" "${GIVEN[@]}" --set account.adminEmail=Root@Ops.Example.invalid)
check "admin email value" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ADMIN_EMAIL"] == "root@ops.example.invalid" and env("chatwoot-gate", "login-bridge")["RESERVED_EMAILS"] == "root@ops.example.invalid"'
OUT=$(render "${SSO[@]}" "${OBC[@]}" "${GIVEN[@]}" --set 'sso.deniedEmails={Demo@Example.com,guest@example.com}')
check "denied emails refused by the bridge" 'env("chatwoot-gate", "login-bridge")["RESERVED_EMAILS"] == "superadmin@chatwoot.invalid,demo@example.com,guest@example.com"'

# --- secrets from a store
OUT=$(render "${SSO[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault)
check "external secrets" 'not objs("Secret") and sorted(e["metadata"]["name"] for e in objs("ExternalSecret")) == ["chatwoot-credentials", "chatwoot-oauth", "chatwoot-platform"] and all(d["remoteRef"]["conversionStrategy"] == "Default" and d["remoteRef"]["decodingStrategy"] == "None" and d["remoteRef"]["metadataPolicy"] == "None" for e in objs("ExternalSecret") for d in e["spec"]["data"])'

# --- values that cannot work
ODOO=(--set odoo.enabled=true --set odoo.url=https://odoo.example.org --set route.hostname=support.example.org)
fails "odoo without hostname" "route.hostname is required with odoo.enabled" --set odoo.enabled=true --set odoo.url=https://odoo.example.org --set secrets.odooApiKey=k
fails "odoo without key" "odoo-api-key of Secret chatwoot-odoo has no value" "${ODOO[@]}"
fails "odoo over http" "/odoo/url" "${ODOO[@]}" --set odoo.url=http://odoo.example.org --set secrets.odooApiKey=k
fails "odoo one inbox for both" "must differ" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.leadInbox=Support
fails "website without url" "odoo.website.url" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true
fails "website inbox is an odoo inbox" "must differ from odoo.ticketInbox" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org --set odoo.website.inbox=Support
fails "website conversations 0" "minimum: got 0, want 1" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org --set odoo.website.conversationsPerMinute=0
fails "website limit 0" "minimum: got 0, want 1" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org --set odoo.website.ticketsPerHour=0
fails "website visitor partner empty" "minLength: got 0, want 1" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org --set odoo.website.visitorPartner=
fails "odoo and standalone" "exclude" "${ODOO[@]}" --set secrets.odooApiKey=k --set agentBridge.standalone.enabled=true --set agentBot.enabled=true --set agent.url=https://a.example.com/ --set agentBridge.standalone.chatwootUrl=https://cw.example.com
DRAFT=(--set draftAgent.enabled=true --set 'draftAgent.inboxes={Support}' --set agent.url=https://a.example.com/a2a/
  --set agent.auth.type=clientSecret --set agent.auth.tokenUrl=https://login.example.com/token
  --set agent.auth.clientId=chatwoot-bridge --set secrets.agentClientSecret=s)
fails "draft without inboxes" "draftAgent.inboxes is required" --set draftAgent.enabled=true --set agent.url=https://a.example.com/
fails "draft with a remote bridge" "needs the bridge in this release" "${DRAFT[@]}" --set agentBridge.remoteUrl=https://bridge.example.org
fails "draft author outside .invalid" "draftAgent.email must be an address under the reserved domain .invalid" "${DRAFT[@]}" --set draftAgent.email=ai@example.com
fails "client secret without token url" "required with agent.auth.type clientSecret" "${DRAFT[@]}" --set agent.auth.tokenUrl=
fails "client secret without value" "agent-client-secret of Secret chatwoot-agent-client has no value" "${DRAFT[@]}" --set secrets.agentClientSecret=
fails "bucket without class" "storageClassName is required" --set attachments.storage=objectBucketClaim
fails "unknown storage" "value must be one of 'volume', 'objectBucketClaim'" --set attachments.storage=s3
fails "sso without issuer" "sso.issuerUrl is required" --set sso.enabled=true
fails "sso over http" "must be an https address" --set sso.enabled=true --set sso.issuerUrl=http://login.example.com
fails "external without store" "storeName is required" --set secrets.type=externalSecret
# below 25 hours the dashboard session cookie expires at once and every login loops
for hours in 12 24 721; do
  fails "session of $hours hours" "/sso/session/chatwootHours" "${SSO[@]}" --set "sso.session.chatwootHours=$hours"
  fails "session of $hours hours past the schema" "must be a whole number of hours from 25 to 720" \
    "${SSO[@]}" --skip-schema-validation --set "sso.session.chatwootHours=$hours"
done
fails "session hours as text" "/sso/session/chatwootHours" "${SSO[@]}" --set-string sso.session.chatwootHours=36
OUT=$(render "${SSO[@]}" --set sso.session.chatwootHours=25)
check "session of 25 hours" 'env("chatwoot-web", "web")["CHATWOOT_SESSION_HOURS"] == "25"'
for bad in boss@example.com admin@chatwoot.invalid.example.com "x@invalid" "a b@x.invalid"; do
  fails "deliverable admin email $bad" "reserved domain .invalid" --set "account.adminEmail=$bad"
done

# --- odoo bridge
OUT=$(render)
check "no odoo bridge by default" 'not obj("Deployment", "chatwoot-odoo-bridge") and not obj("Secret", "chatwoot-odoo") and "CHART_ODOO_BRIDGE_URL" not in env("chatwoot-worker", "worker") and "odoo_bridge.py" not in obj("ConfigMap", "chatwoot-scripts")["data"]'
OUT=$(render "${ODOO[@]}" --set secrets.odooApiKey=k)
check "odoo bridge one pod at a time" 'obj("Deployment", "chatwoot-odoo-bridge")["spec"]["strategy"] == {"type": "Recreate"}'
check "odoo bridge env" 'env("chatwoot-odoo-bridge", "odoo-bridge")["ODOO_URL"] == "https://odoo.example.org" and env("chatwoot-odoo-bridge", "odoo-bridge")["PUBLIC_URL"] == "https://support.example.org" and env("chatwoot-odoo-bridge", "odoo-bridge")["TICKET_INBOX"] == "Support"'
check "odoo inboxes set up by the job" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_WEBHOOK_URL"] == "http://chatwoot-odoo-bridge:8080/chatwoot/webhook" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_INBOXES"] == "Support,Sales"'
check "no website chat by default" '"RATE_LIMIT_WIDGET_CONVERSATIONS" not in env("chatwoot-web", "web") and "WEBSITE_INBOX" not in env("chatwoot-odoo-bridge", "odoo-bridge") and "CHATWOOT_ODOO_WEBSITE_INBOX" not in env("chatwoot-prepare-1", "prepare") and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_WEBSITE_WEBHOOK_URL"] == "http://chatwoot-odoo-bridge:8080/chatwoot/website"'
WEB=$(render "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org/ --set odoo.website.team=Support)
OUT_KEEP=$OUT; OUT=$WEB
check "website chat env" 'env("chatwoot-odoo-bridge", "odoo-bridge")["WEBSITE_INBOX"] == "Website" and env("chatwoot-odoo-bridge", "odoo-bridge")["WEBSITE_TEAM"] == "Support" and env("chatwoot-odoo-bridge", "odoo-bridge")["WEBSITE_TICKETS_PER_EMAIL"] == "3" and env("chatwoot-odoo-bridge", "odoo-bridge")["WEBSITE_VISITOR_PARTNER"] == "Website visitor" and env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_LABEL"] == "sales" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_WEBSITE_INBOX"] == "Website" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_WEBSITE_URL"] == "https://www.example.org"'
check "website chat: the job keeps the sales label" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_SALES_LABEL"] == "sales"'
check "website chat: the chart's own users are not staff, the draft user also while off" 'env("chatwoot-odoo-bridge", "odoo-bridge")["NOT_STAFF_EMAILS"] == "odoo-bridge@chatwoot.invalid,ai-draft@chatwoot.invalid" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_BRIDGE_EMAIL"] == "odoo-bridge@chatwoot.invalid"'
WEBD=$(render "${ODOO[@]}" "${DRAFT[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org/ --set odoo.website.team=Support)
OUT=$WEBD
check "website chat with draft: the draft user is not staff" 'env("chatwoot-odoo-bridge", "odoo-bridge")["NOT_STAFF_EMAILS"] == "odoo-bridge@chatwoot.invalid,ai-draft@chatwoot.invalid"'
OUT=$WEB
check "website chat: widget conversations limited per address" 'all(env(n, c)["RATE_LIMIT_WIDGET_CONVERSATIONS"] == "5" for n, c in (("chatwoot-web", "web"), ("chatwoot-worker", "worker")))'
OUT=$OUT_KEEP
check "odoo key to the bridge only" '"chatwoot-odoo" in refs(pod("chatwoot-odoo-bridge")) and all("chatwoot-odoo\"" not in refs(pod(n)) for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-gate", "chatwoot-prepare-1"))'
check "odoo private webhook" 'all(env(n, c)["CHART_ODOO_BRIDGE_URL"] == "http://chatwoot-odoo-bridge:8080/" for n, c in (("chatwoot-web", "web"), ("chatwoot-worker", "worker")))'
check "odoo events route" 'obj("Route", "chatwoot-odoo-bridge")["spec"]["path"] == "/odoo/events" and obj("Route", "chatwoot-odoo-bridge")["spec"]["port"]["targetPort"] == "events" and obj("Route", "chatwoot-odoo-bridge")["spec"]["host"] == "support.example.org"'
check "odoo webhooks from the worker, events from the router" 'obj("NetworkPolicy", "chatwoot-odoo-bridge")["spec"]["ingress"][0]["from"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-worker"}}}] and obj("NetworkPolicy", "chatwoot-odoo-bridge")["spec"]["ingress"][1]["ports"] == [{"protocol": "TCP", "port": 8081}]'
check "odoo bridge reaches web" '{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-odoo-bridge"}}} in obj("NetworkPolicy", "chatwoot-web")["spec"]["ingress"][0]["from"]'
OUT=$(render "${ODOO[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault)
check "odoo secrets from a store" 'obj("ExternalSecret", "chatwoot-odoo")["spec"]["data"][0]["remoteRef"]["key"] == "chatwoot-odoo-api-key" and obj("ExternalSecret", "chatwoot-odoo-bridge")'
check "no monitoring by default" 'not objs("ServiceMonitor") and not objs("PrometheusRule")'
OUT=$(render "${ODOO[@]}" --set secrets.odooApiKey=k --set monitoring.enabled=true)
check "odoo bridge monitored" 'obj("ServiceMonitor", "chatwoot-odoo-bridge")["spec"]["endpoints"] == [{"port": "webhook", "path": "/metrics", "scheme": "http"}] and len(obj("PrometheusRule", "chatwoot-odoo-bridge")["spec"]["groups"][0]["rules"]) == 3'
check "monitoring reaches the metrics port only" 'obj("NetworkPolicy", "chatwoot-odoo-bridge")["spec"]["ingress"][2] == {"ports": [{"protocol": "TCP", "port": 8080}], "from": [{"namespaceSelector": {"matchLabels": {"network.openshift.io/policy-group": "monitoring"}}}]}'
OUT=$(render "${ODOO[@]}" --set secrets.odooApiKey=k --set monitoring.enabled=true --set monitoring.alerts=false)
check "alerts can stay central" 'obj("ServiceMonitor", "chatwoot-odoo-bridge") and not objs("PrometheusRule")'

# --- agent bot and panel
AGENT=(--set agent.url=https://agents.example.com/a2a/acme --set agent.auth.type=clientAssertion
  --set agent.auth.audience=https://login.example.com/realms/acme
  --set agent.auth.tokenUrl=https://login.example.com/realms/acme/protocol/openid-connect/token
  --set agent.auth.clientId=chatwoot-agent)
OUT=$(render)
check "no bridge by default" 'not obj("Deployment", "chatwoot-agent-bridge") and not obj("ServiceAccount", "chatwoot-agent-bridge") and "CHART_AGENT_BOT_URL" not in env("chatwoot-worker", "worker")'
OUT=$(render "${SSO[@]}" "${AGENT[@]}" --set agentBot.enabled=true --set 'agentBot.inboxes={Website,Mail}')
check "bot bridge" 'pod("chatwoot-agent-bridge")["serviceAccountName"] == "chatwoot-agent-bridge" and env("chatwoot-agent-bridge", "agent-bot")["A2A_AUTH"] == "clientAssertion" and env("chatwoot-agent-bridge", "agent-bot")["A2A_TOKEN_FILE"] == "/var/run/secrets/agent/token"'
check "bot token audience" 'any(v.get("projected", {}).get("sources", [{}])[0].get("serviceAccountToken", {}).get("audience") == "https://login.example.com/realms/acme" for v in pod("chatwoot-agent-bridge")["volumes"])'
check "bot set up by the job" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_AGENT_BOT_URL"] == "http://chatwoot-agent-bridge:8080/webhook" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_AGENT_BOT_INBOXES"] == "Website,Mail"'
check "bot secret to bridge and job only" '"chatwoot-agent-bot" in refs(pod("chatwoot-agent-bridge")) and "chatwoot-agent-bot" in refs(pod("chatwoot-prepare-1")) and all("chatwoot-agent-bot\"" not in refs(pod(n)) for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-gate"))'
check "private webhook to the bridge only" 'all(env(n, c)["CHART_AGENT_BOT_URL"] == "http://chatwoot-agent-bridge:8080/" for n, c in (("chatwoot-web", "web"), ("chatwoot-worker", "worker")))'
check "webhooks from the worker only" 'obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8080}], "from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-worker"}}}]}]'
check "bridge reaches web" '{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-agent-bridge"}}} in obj("NetworkPolicy", "chatwoot-web")["spec"]["ingress"][0]["from"]'
check "no panel without the value" 'not ctr("chatwoot-gate", "agent-panel")'
OUT=$(render "${SSO[@]}" "${AGENT[@]}" --set copilotPanel.enabled=true)
check "panel only: no bot" 'not obj("Deployment", "chatwoot-agent-bridge") and not obj("Secret", "chatwoot-agent-bot") and "CHATWOOT_AGENT_BOT_TOKEN" not in env("chatwoot-prepare-1", "prepare")'
check "panel behind the login" '"--upstream=http://127.0.0.1:8082/agent-panel/" in args() and env("chatwoot-gate", "agent-panel")["PANEL_HOST"] == "127.0.0.1" and not ctr("chatwoot-gate", "agent-panel").get("ports")'
check "panel needs a login" 'not public("GET", "/agent-panel/") and not public("POST", "/agent-panel/api")'
check "panel identity" 'pod("chatwoot-gate")["serviceAccountName"] == "chatwoot-agent-bridge" and pod("chatwoot-gate")["automountServiceAccountToken"] is False'
check "panel registered by the job" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_AGENT_PANEL_PATH"] == "/agent-panel/"'
OUT=$(render "${SSO[@]}" "${AGENT[@]}" --set copilotPanel.enabled=true --set route.hostname=)
check "panel without hostname reads the Route" 'pod("chatwoot-gate")["automountServiceAccountToken"] is True and {"kind": "ServiceAccount", "name": "chatwoot-agent-bridge"} in obj("RoleBinding", "chatwoot-route")["subjects"]'
OUT=$(render "${SSO[@]}" --set agentBot.enabled=true --set agent.url=http://agent.acme.svc:8080/ --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault)
check "bot secrets from a store" 'obj("ExternalSecret", "chatwoot-agent-bot")["spec"]["data"][0]["remoteRef"]["key"] == "chatwoot-agent-bot-token" and not obj("Deployment", "chatwoot-gate").get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [{}])[0].get("projected")'
check "bridge egress to an in-cluster agent" 'obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"][3] == {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "acme"}}}]}'
# --- bridge in another release (Chatwoot side) and the standalone bridge
# --- AI drafts: private notes through the bridge here, client secret for the agent
OUT=$(render "${DRAFT[@]}")
check "draft bridge env" 'env("chatwoot-agent-bridge", "agent-bot")["DRAFT_INBOXES"] == "Support" and env("chatwoot-agent-bridge", "agent-bot")["DRAFT_TOKEN_FILE"] == "/etc/chatwoot-draft/draft-token" and "BOT_TOKEN_FILE" not in env("chatwoot-agent-bridge", "agent-bot")'
check "draft client secret" 'env("chatwoot-agent-bridge", "agent-bot")["A2A_AUTH"] == "clientSecret" and env("chatwoot-agent-bridge", "agent-bot")["A2A_CLIENT_SECRET_FILE"] == "/var/run/secrets/agent/client-secret" and secret("chatwoot-agent-client", "agent-client-secret") == "s" and not any(v.get("projected") for v in pod("chatwoot-agent-bridge")["volumes"])'
check "draft secrets" 'len(secret("chatwoot-draft", "draft-token")) == 48 and len(secret("chatwoot-draft", "draft-webhook-secret")) == 48'
check "draft job" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_DRAFT_WEBHOOK_URL"] == "http://chatwoot-agent-bridge:8080/draft" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_DRAFT_EMAIL"] == "ai-draft@chatwoot.invalid"'
check "draft webhook may reach the bridge" 'all(env(n, c)["CHART_AGENT_BOT_URL"] == "http://chatwoot-agent-bridge:8080/" for n, c in (("chatwoot-web", "web"), ("chatwoot-worker", "worker"))) and env("chatwoot-prepare-1", "prepare")["CHATWOOT_DRAFT_WEBHOOK_URL"].startswith(env("chatwoot-worker", "worker")["CHART_AGENT_BOT_URL"])'
check "bridge egress to chatwoot, dns and public 443 only" 'set(obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["policyTypes"]) == {"Ingress", "Egress"} and [r["to"] for r in obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"]][2] == [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16"]}}] and obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"][2]["ports"] == [{"protocol": "TCP", "port": 443}] and obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"][0]["to"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-web"}}}] and all("Egress" not in n["spec"]["policyTypes"] for n in objs("NetworkPolicy") if n["metadata"]["name"] != "chatwoot-agent-bridge")'
check "draft network" 'obj("NetworkPolicy", "chatwoot-agent-bridge") and "chatwoot-agent-bridge" in refs(obj("NetworkPolicy", "chatwoot-web"))'
OUT=$(render "${DRAFT[@]}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault)
check "draft external secrets" '{"chatwoot-draft", "chatwoot-agent-client"} <= {e["metadata"]["name"] for e in objs("ExternalSecret")}'
OUT=$(render "${DRAFT[@]}" --set-json 'networkPolicy.agentEgress.to=[{"ipBlock":{"cidr":"203.0.113.7/32"}}]' --set-json 'networkPolicy.agentEgress.ports=[8443]')
check "bridge egress to the named agent alone" 'obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"][2:] == [{"ports": [{"protocol": "TCP", "port": 8443}], "to": [{"ipBlock": {"cidr": "203.0.113.7/32"}}]}]'

REMOTE=(--set agentBridge.remoteUrl=https://bridge.example.org --set agentBot.enabled=true --set copilotPanel.enabled=true)
OUT=$(render "${SSO[@]}" "${REMOTE[@]}")
check "remote: no bridge here" 'not obj("Deployment", "chatwoot-agent-bridge") and not obj("ServiceAccount", "chatwoot-agent-bridge") and obj("Secret", "chatwoot-agent-bot")'
check "remote: webhook to the remote bridge" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_AGENT_BOT_URL"] == "https://bridge.example.org/webhook" and "CHART_AGENT_BOT_URL" not in env("chatwoot-worker", "worker")'
check "remote: bot answers through the login" 'public("POST", "/api/v1/accounts/1/conversations/12/messages") and public("POST", "/api/v1/accounts/1/conversations/12/toggle_status") and not public("GET", "/api/v1/accounts/1/conversations/12/messages") and not public("POST", "/api/v1/accounts/1/contacts")'
check "remote: panel forwards the staff token" 'env("chatwoot-gate", "agent-panel")["PANEL_FORWARD_URL"] == "https://bridge.example.org/agent-panel/api" and "A2A_URL" not in env("chatwoot-gate", "agent-panel") and "--pass-access-token=true" in args() and pod("chatwoot-gate").get("serviceAccountName") is None'
OUT=$(render "${SSO[@]}" "${AGENT[@]}" --set copilotPanel.enabled=true)
check "local panel: no access token passed" '"--pass-access-token=false" in args() and "--upstream-timeout=330s" in args() and obj("Route", "chatwoot")["metadata"]["annotations"]["haproxy.router.openshift.io/timeout"] == "330s"'
STANDALONE=(--set agentBridge.standalone.enabled=true --set agentBot.enabled=true --set copilotPanel.enabled=true
  --set agentBridge.standalone.chatwootUrl=https://cw.example.com --set agentBridge.standalone.route.hostname=bridge.example.org
  --set agentBridge.standalone.staff.issuerUrl=https://login.example.com/realms/acme --set 'agentBridge.standalone.staff.clientIds={cw.example.com}'
  --set 'agentBridge.standalone.staff.allowedGroups={support}' --set agent.url=https://agents.example.com/a2a/x
  --set agent.auth.type=serviceAccountToken --set agent.auth.audience=agents.example.com)
OUT=$(render "${STANDALONE[@]}")
check "standalone: the bridge alone" '{(d["kind"], d["metadata"]["name"]) for d in docs} == {("ConfigMap", "chatwoot-scripts"), ("Deployment", "chatwoot-agent-bridge"), ("NetworkPolicy", "chatwoot-agent-bridge"), ("Route", "chatwoot-agent-bridge"), ("Secret", "chatwoot-agent-bot"), ("Service", "chatwoot-agent-bridge"), ("ServiceAccount", "chatwoot-agent-bridge")}'
check "standalone: answers to the public Chatwoot, staff tokens checked" 'env("chatwoot-agent-bridge", "agent-bot")["CHATWOOT_URL"] == "https://cw.example.com" and env("chatwoot-agent-bridge", "agent-bot")["STAFF_CLIENTS"] == "cw.example.com" and env("chatwoot-agent-bridge", "agent-bot")["STAFF_GROUPS"] == "support"'
check "standalone: route with a long timeout" 'obj("Route", "chatwoot-agent-bridge")["spec"]["host"] == "bridge.example.org" and obj("Route", "chatwoot-agent-bridge")["metadata"]["annotations"]["haproxy.router.openshift.io/timeout"] == "330s" and obj("Route", "chatwoot-agent-bridge")["spec"]["tls"]["termination"] == "edge"'
check "standalone: router only" 'obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["ingress"][0]["from"] == [{"namespaceSelector": {"matchLabels": {"policy-group.network.openshift.io/ingress": ""}}}]'
fails "standalone without feature" "needs agentBot.enabled or copilotPanel.enabled" --set agentBridge.standalone.enabled=true
fails "standalone without chatwoot" "standalone.chatwootUrl, an https address, is required" --set agentBridge.standalone.enabled=true --set agentBot.enabled=true --set agent.url=https://a.example.com/
fails "standalone panel without staff" "staff.issuerUrl (https) and staff.clientIds are required" --set agentBridge.standalone.enabled=true --set copilotPanel.enabled=true --set agent.url=https://a.example.com/ --set agentBridge.standalone.chatwootUrl=https://cw.example.com
fails "remote over http" "agentBridge.remoteUrl must be an https address" --set agentBridge.remoteUrl=http://bridge.example.org --set agentBot.enabled=true
fails "remote and standalone" "exclude each other" --set agentBridge.remoteUrl=https://bridge.example.org --set agentBridge.standalone.enabled=true --set agentBot.enabled=true
fails "bot without agent" "agent.url, an http or https address, is required" --set agentBot.enabled=true
fails "panel without sso" "copilotPanel.enabled needs sso.enabled" --set copilotPanel.enabled=true --set agent.url=https://a.example.com/
fails "assertion without client" "tokenUrl and agent.auth.clientId are required" --set agentBot.enabled=true --set agent.url=https://a.example.com/ --set agent.auth.type=clientAssertion --set agent.auth.audience=x
fails "token without audience" "agent.auth.audience is required" --set agentBot.enabled=true --set agent.url=https://a.example.com/ --set agent.auth.type=serviceAccountToken

# --- AI triage: categories, teams and tenant facts on the draft path
TRIAGE=("${DRAFT[@]}" --set triage.enabled=true --set triage.fallback=engineering
  --set-json 'triage.categories=[{"name":"engineering","team":"Engineering","description":"errors"},{"name":"general"}]'
  --set-json 'triage.teams={"engineering":["ann@example.com"],"sales":["bob@example.com"]}')
FACTS=(--set tenantFacts.enabled=true --set tenantFacts.emailAnnotation=example.com/email --set tenantFacts.tenantLabel=example.com/tenant)
OUT=$(render "${DRAFT[@]}")
check "sweep with drafts" 'env("chatwoot-agent-bridge", "agent-bot")["SWEEP_SECONDS"] == "60" and env("chatwoot-agent-bridge", "agent-bot")["CHATWOOT_ACCOUNT_ID"] == "1" and "TRIAGE_CATEGORIES" not in env("chatwoot-agent-bridge", "agent-bot") and "CHATWOOT_TRIAGE_LABELS" not in env("chatwoot-prepare-1", "prepare")'
check "no kube access without facts" 'not any(v["name"] == "kube" for v in pod("chatwoot-agent-bridge")["volumes"]) and pod("chatwoot-agent-bridge")["automountServiceAccountToken"] is False and "6443" not in refs(obj("NetworkPolicy", "chatwoot-agent-bridge"))'
OUT=$(render "${TRIAGE[@]}")
check "triage bridge env" 'json.loads(env("chatwoot-agent-bridge", "agent-bot")["TRIAGE_CATEGORIES"])[0] == {"name": "engineering", "team": "Engineering", "description": "errors"} and env("chatwoot-agent-bridge", "agent-bot")["TRIAGE_FALLBACK"] == "engineering" and env("chatwoot-agent-bridge", "agent-bot")["TRIAGE_LABEL"] == "ai-triaged"'
check "triage job: labels and teams" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_TRIAGE_LABELS"] == "engineering,general,ai-triaged" and json.loads(env("chatwoot-prepare-1", "prepare")["CHATWOOT_TRIAGE_TEAMS"]) == {"engineering": ["ann@example.com"], "sales": ["bob@example.com"]}'
check "triage without facts reads no cluster" 'not any(v["name"] == "kube" for v in pod("chatwoot-agent-bridge")["volumes"]) and "FACTS_EMAIL_ANNOTATION" not in env("chatwoot-agent-bridge", "agent-bot")'
OUT=$(render "${TRIAGE[@]}" "${FACTS[@]}" --set-json 'networkPolicy.apiServerEgress.to=[{"ipBlock":{"cidr":"172.20.0.1/32"}}]')
check "facts env" 'env("chatwoot-agent-bridge", "agent-bot")["FACTS_EMAIL_ANNOTATION"] == "example.com/email" and env("chatwoot-agent-bridge", "agent-bot")["KUBE_TOKEN_FILE"] == "/var/run/secrets/kube/token" and env("chatwoot-agent-bridge", "agent-bot")["FACTS_VELERO_NAMESPACE"] == "" and env("chatwoot-agent-bridge", "agent-bot")["FACTS_IGNORE_EVENT_REASONS"] == ""'
check "facts token: projected, cluster CA" '[v for v in pod("chatwoot-agent-bridge")["volumes"] if v["name"] == "kube"][0]["projected"]["sources"] == [{"serviceAccountToken": {"expirationSeconds": 3600, "path": "token"}}, {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}}] and pod("chatwoot-agent-bridge")["automountServiceAccountToken"] is False'
check "facts egress: the API alone" 'obj("NetworkPolicy", "chatwoot-agent-bridge")["spec"]["egress"][-1] == {"ports": [{"protocol": "TCP", "port": 6443}], "to": [{"ipBlock": {"cidr": "172.20.0.1/32"}}]}'
check "facts: no cluster role from the chart" 'not objs("ClusterRole") and not objs("ClusterRoleBinding")'
OUT=$(render "${TRIAGE[@]}" "${FACTS[@]}" --set-json 'tenantFacts.ignoreEventReasons=["PolicyViolation","FailedMount"]')
check "facts ignored reasons" 'env("chatwoot-agent-bridge", "agent-bot")["FACTS_IGNORE_EVENT_REASONS"] == "PolicyViolation,FailedMount"'
fails "triage without drafts" "triage.enabled needs draftAgent.enabled" --set triage.enabled=true
fails "triage without categories" "triage.categories is required" "${DRAFT[@]}" --set triage.enabled=true
fails "triage category no label" "does not match pattern" "${TRIAGE[@]}" --set-json 'triage.categories=[{"name":"Engi neering"}]' --set triage.fallback=x
fails "triage category twice" "names a category twice" "${TRIAGE[@]}" --set-json 'triage.categories=[{"name":"a"},{"name":"a"}]' --set triage.fallback=a
fails "triage fallback unknown" "triage.fallback must be the name" "${TRIAGE[@]}" --set triage.fallback=weather
fails "triage label is a category" "names no category" "${TRIAGE[@]}" --set triage.label=general
WEBSITE=("${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.website.enabled=true --set odoo.website.url=https://www.example.org)
fails "sales label is a triage category" "must differ from triage.label" "${TRIAGE[@]}" "${WEBSITE[@]}" --set-json 'triage.categories=[{"name":"engineering"},{"name":"sales"}]'
fails "sales label is the triage label" "must differ from triage.label" "${TRIAGE[@]}" "${WEBSITE[@]}" --set triage.label=sales
fails "sales label with a space" "does not match pattern" "${WEBSITE[@]}" --set "odoo.website.salesLabel=to sales"
OUT=$(render "${WEBSITE[@]}" --set odoo.website.salesLabel=Lead)
check "sales label with capitals renders" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_SALES_LABEL"] == "Lead"'
OUT=$(render "${TRIAGE[@]}" "${WEBSITE[@]}")
check "website chat with triage" 'env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_LABEL"] == "sales"'
OUT=$(render "${WEBSITE[@]}" --set odoo.website.salesLabel=)
check "website chat without sales label: the job keeps none" '"CHATWOOT_ODOO_SALES_LABEL" not in env("chatwoot-prepare-1", "prepare") and env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_LABEL"] == ""'
fails "triage heading like the draft's" "must not start like draftAgent.heading" "${TRIAGE[@]}" --set "triage.heading=AI draft (check before sending): triage"
fails "facts without triage" "tenantFacts.enabled needs triage.enabled" "${DRAFT[@]}" "${FACTS[@]}"
fails "facts without annotation" "tenantFacts.emailAnnotation and tenantFacts.tenantLabel are required" "${TRIAGE[@]}" --set tenantFacts.enabled=true

# --- the sales label on support conversations
SUP=("${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.sales.fromSupport=true)
OUT=$(render "${SUP[@]}" --set odoo.sales.team=Direct)
check "support to sales: bridge env" 'env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_FROM_SUPPORT"] == "true" and env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_LABEL"] == "sales" and env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_TEAM"] == "Direct" and env("chatwoot-odoo-bridge", "odoo-bridge")["CANCELLED_STAGE"] == "Cancelled" and "odoo-bridge@" in env("chatwoot-odoo-bridge", "odoo-bridge")["NOT_STAFF_EMAILS"] and "WEBSITE_INBOX" not in env("chatwoot-odoo-bridge", "odoo-bridge")'
check "support to sales: the job keeps the label, no website" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_SALES_LABEL"] == "sales" and "CHATWOOT_ODOO_WEBSITE_INBOX" not in env("chatwoot-prepare-1", "prepare")'
OUT=$(render "${ODOO[@]}" --set secrets.odooApiKey=k)
check "no sales label without website or support" '"SALES_LABEL" not in env("chatwoot-odoo-bridge", "odoo-bridge") and "CHATWOOT_ODOO_SALES_LABEL" not in env("chatwoot-prepare-1", "prepare")'
OUT=$(render "${WEBSITE[@]}")
check "website only: support conversations stay" 'env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_FROM_SUPPORT"] == "false" and env("chatwoot-odoo-bridge", "odoo-bridge")["SALES_TEAM"] == ""'
fails "support to sales without a lead inbox" "needs odoo.leadInbox" "${SUP[@]}" --set odoo.leadInbox=
fails "support to sales without a label" "needs odoo.website.salesLabel" "${SUP[@]}" --set odoo.website.salesLabel=
fails "support to sales with a triage label" "must differ from triage.label" "${SUP[@]}" "${TRIAGE[@]}" --set odoo.website.salesLabel=general

# --- Odoo side panel
PANEL=("${SSO[@]}" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.panel.enabled=true)
OUT=$(render "${PANEL[@]}")
check "panel: container on loopback behind the login" 'env("chatwoot-gate", "odoo-panel")["PANEL_HOST"] == "127.0.0.1" and "ports" not in ctr("chatwoot-gate", "odoo-panel") and "--upstream=http://127.0.0.1:8083/odoo-panel/" in args()'
check "panel: its own Secret, no event secret" 'set(obj("Secret", "chatwoot-odoo-panel")["data"]) == {"odoo-api-key"} and secret("chatwoot-odoo-panel", "odoo-api-key") == "k" and "\"chatwoot-odoo\"" not in refs(pod("chatwoot-gate"))'
check "panel: reads conversations as the staff member" 'env("chatwoot-gate", "odoo-panel")["PLATFORM_TOKEN_FILE"] == "/etc/chatwoot-platform/platform-token" and {"name": "platform", "mountPath": "/etc/chatwoot-platform", "readOnly": True} in ctr("chatwoot-gate", "odoo-panel")["volumeMounts"] and env("chatwoot-gate", "odoo-panel")["RESERVED_EMAILS"] == env("chatwoot-gate", "login-bridge")["RESERVED_EMAILS"]'
check "panel: scripts" '"odoo_panel.py" in obj("ConfigMap", "chatwoot-scripts")["data"] and "odoo_panel.html" in obj("ConfigMap", "chatwoot-scripts")["data"]'
check "panel: dashboard app" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_PANEL_TITLE"] == "Odoo" and env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_PANEL_PATH"] == "/odoo-panel/"'
check "panel: no issues, no summary" '"ISSUES_URL" not in env("chatwoot-gate", "odoo-panel") and env("chatwoot-gate", "odoo-panel")["SUMMARY"] == "0" and env("chatwoot-gate", "odoo-panel")["PUBLIC_URL"] == "https://support.example.org"'
OUT=$(render "${PANEL[@]}" --set odoo.panel.issues.url=https://example.atlassian.net/ --set odoo.panel.issues.user=bot@example.com --set secrets.issueTrackerToken=t)
check "panel: issues" 'env("chatwoot-gate", "odoo-panel")["ISSUES_URL"] == "https://example.atlassian.net" and env("chatwoot-gate", "odoo-panel")["ISSUES_USER"] == "bot@example.com" and secret("chatwoot-odoo-panel", "issue-tracker-token") == "t"'
OUT=$(render "${PANEL[@]}" "${AGENT[@]}" --set copilotPanel.enabled=true)
check "panel: summary with the agent panel" 'env("chatwoot-gate", "odoo-panel")["SUMMARY"] == "1"'
OUT=$(render "${SSO[@]}" "${ODOO[@]}" --set secrets.odooApiKey=k)
check "no panel by default" 'not ctr("chatwoot-gate", "odoo-panel") and not obj("Secret", "chatwoot-odoo-panel") and "CHATWOOT_ODOO_PANEL_PATH" not in env("chatwoot-prepare-1", "prepare")'
fails "panel without sso" "odoo.panel.enabled needs sso.enabled" "${ODOO[@]}" --set secrets.odooApiKey=k --set odoo.panel.enabled=true
fails "panel issues without token" "issue-tracker-token of Secret chatwoot-odoo-panel has no value" "${PANEL[@]}" --set odoo.panel.issues.url=https://example.atlassian.net
fails "panel issues over http" "/odoo/panel/issues/url" "${PANEL[@]}" --set odoo.panel.issues.url=http://example.atlassian.net
fails "panel title like the agent panel's" "must differ" "${PANEL[@]}" "${AGENT[@]}" --set copilotPanel.enabled=true --set odoo.panel.title=Agent

# --- logged-in customers on the website: identity validation, the bot's own agent and records
IDENT=("${WEBSITE[@]}" --set odoo.website.identity.enabled=true --set agentBot.enabled=true
  --set agent.url=https://a.example.com/ --set agentBot.agentUrl=https://a.example.com/www/ --set 'agentBot.inboxes={Website}')
OUT=$(render "${IDENT[@]}" --set secrets.websiteIdentityKey=wk)
check "identity: key to the job only" 'secret("chatwoot-website-identity", "website-identity-key") == "wk" and "chatwoot-website-identity" in refs(pod("chatwoot-prepare-1")) and not any("chatwoot-website-identity" in refs(pod(n)) for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-agent-bridge", "chatwoot-odoo-bridge"))'
check "identity: the bot asks its own agent" 'env("chatwoot-agent-bridge", "agent-bot")["BOT_A2A_URL"] == "https://a.example.com/www/" and env("chatwoot-agent-bridge", "agent-bot")["BOT_INBOXES"] == "Website"'
check "identity: records with the bridge key" 'env("chatwoot-agent-bridge", "agent-bot")["IDENTITY_RECORDS"] == "1" and env("chatwoot-agent-bridge", "agent-bot")["ODOO_URL"] == "https://odoo.example.org" and secret("chatwoot-agent-odoo", "odoo-api-key") == "k" and "chatwoot-agent-odoo" in refs(pod("chatwoot-agent-bridge"))'
OUT=$(render "${IDENT[@]}" --set odoo.website.identity.records=false)
check "identity: records off" '"IDENTITY_RECORDS" not in env("chatwoot-agent-bridge", "agent-bot") and not obj("Secret", "chatwoot-agent-odoo")'
OUT=$(render "${WEBSITE[@]}")
check "identity: off by default" 'not obj("Secret", "chatwoot-website-identity") and "CHATWOOT_WEBSITE_IDENTITY_KEY" not in env("chatwoot-prepare-1", "prepare")'

# --- the customer page at / of the host, its login and the pre-chat message
OUT=$(render "${SSO[@]}")
check "customer page: off by default" 'not public("GET", "/") and not public("POST", "/customer/identity") and "CHART_CUSTOMER_PAGE" not in env("chatwoot-web", "web") and "customer_page.rb" not in obj("ConfigMap", "chatwoot-scripts")["data"]'
CP=("${SSO[@]}" --set sso.public.customerPage.enabled=true --set sso.public.customerPage.portalUrl=https://portal.example.com/my
  --set sso.public.customerPage.login.issuerUrl=https://login.example.com/realms/customers --set sso.public.customerPage.login.clientId=site)
OUT=$(render "${CP[@]}")
check "customer page: / and its login are public, nothing else" 'public("GET", "/") and public("POST", "/customer/identity") and not any(public(m, p) for m, p in [("POST", "/"), ("GET", "/app"), ("GET", "/app/login"), ("GET", "/customer/identity"), ("POST", "/customer/identity/x"), ("POST", "/customer"), ("GET", "/index.html"), ("GET", "/super_admin"), ("POST", "/auth/sign_in")])'
check "customer page: web serves it" 'env("chatwoot-web", "web")["CHART_CUSTOMER_PAGE"] == "/scripts/customer_page.html" and json.loads(env("chatwoot-web", "web")["CHART_CUSTOMER_PAGE_CONFIG"]) == {"accountId": 1, "inbox": "Website", "title": "Support", "portalUrl": "https://portal.example.com/my", "portalText": "Orders, quotations and invoices", "staffUrl": "/app/login", "issuerUrl": "https://login.example.com/realms/customers", "clientId": "site"} and {"customer_page.rb", "customer_page.html"} <= set(obj("ConfigMap", "chatwoot-scripts")["data"]) and "CHART_CUSTOMER_PAGE" not in env("chatwoot-worker", "worker")'
check "customer page: no key outside the job" 'not any("chatwoot-website-identity" in refs(pod(n)) for n in ("chatwoot-web", "chatwoot-gate", "chatwoot-worker"))'
fails "customer page: login needs both" "needs both issuerUrl" "${SSO[@]}" --set sso.public.customerPage.enabled=true --set sso.public.customerPage.login.clientId=site
fails "customer page: https portal" "portalUrl" "${SSO[@]}" --set sso.public.customerPage.enabled=true --set sso.public.customerPage.portalUrl=http://portal.example.com
fails "customer page: needs the widget" "needs sso.public.widget" "${SSO[@]}" --set sso.public.customerPage.enabled=true --set sso.public.widget=false
OUT=$(render "${WEBSITE[@]}" --set 'odoo.website.preChatMessage=Customer? [Log in](https://example.org/?chat-login=1)')
check "pre-chat message to the job" 'env("chatwoot-prepare-1", "prepare")["CHATWOOT_ODOO_WEBSITE_PRE_CHAT_MESSAGE"] == "Customer? [Log in](https://example.org/?chat-login=1)"'
OUT=$(render "${WEBSITE[@]}")
check "pre-chat message: unset keeps Chatwoot's" '"CHATWOOT_ODOO_WEBSITE_PRE_CHAT_MESSAGE" not in env("chatwoot-prepare-1", "prepare")'

# --- the MCP server: tools for AI agents behind kube-rbac-proxy
OUT=$(render)
check "mcp: off by default" 'not any("chatwoot-mcp" in d["metadata"]["name"] for d in docs) and "chatwoot-mcp" not in refs(obj("NetworkPolicy", "chatwoot-web"))'
MCP=(--set mcp.enabled=true --set mcp.image.repository=registry.example.com/chatwoot-mcp --set mcp.image.tag=1.0.0 --set secrets.mcpToken=mt)
fails "mcp: no caller" "needs mcp.callers or mcp.callerAccount.enabled" "${MCP[@]}"
fails "mcp: no image" "mcp.image.repository is required" --set mcp.enabled=true --set mcp.callerAccount.enabled=true --set "mcp.allowedHosts={gw.example.com}" --set secrets.mcpToken=mt
fails "mcp: no token" "mcp-token of Secret chatwoot-mcp has no value" --set mcp.enabled=true --set mcp.callerAccount.enabled=true --set "mcp.allowedHosts={gw.example.com}" --set mcp.image.repository=r --set mcp.image.tag=v1
fails "mcp: not standalone" "mcp.enabled and agentBridge.standalone.enabled exclude each other" "${MCP[@]}" --set mcp.callerAccount.enabled=true --set "mcp.allowedHosts={gw.example.com}" --set agentBridge.standalone.enabled=true --set agentBridge.standalone.chatwootUrl=https://cw.example.com --set agentBot.enabled=true --set agent.url=https://a.example.com/
OUT=$(render "${MCP[@]}" --set 'mcp.callers[0].kind=ServiceAccount' --set 'mcp.callers[0].name=gw' --set 'mcp.callers[0].namespace=gateway' --set 'mcp.noteInboxIds={2,5}')
check "mcp: server calls web inside the release" 'ctr("chatwoot-mcp", "server")["image"] == "registry.example.com/chatwoot-mcp:1.0.0" and env("chatwoot-mcp", "server")["CHATWOOT_URL"] == "http://chatwoot-web:3000" and env("chatwoot-mcp", "server")["CHATWOOT_ACCOUNT_ID"] == "1" and env("chatwoot-mcp", "server")["CHATWOOT_NOTE_INBOX_IDS"] == "2,5"'
check "mcp: service hosts for in-cluster callers" 'env("chatwoot-mcp", "server")["MCP_ALLOWED_HOSTS"] == "chatwoot-mcp,chatwoot-mcp:*,chatwoot-mcp.test,chatwoot-mcp.test:*,chatwoot-mcp.test.svc,chatwoot-mcp.test.svc:*,chatwoot-mcp.test.svc.cluster.local,chatwoot-mcp.test.svc.cluster.local:*" and "MCP_ORGANIZATION" not in env("chatwoot-mcp", "server")'
check "mcp: server on loopback, proxy on 8443" 'env("chatwoot-mcp", "server")["MCP_HOST"] == "127.0.0.1" and "ports" not in ctr("chatwoot-mcp", "server") and ctr("chatwoot-mcp", "kube-rbac-proxy")["ports"] == [{"name": "mcp", "containerPort": 8443, "protocol": "TCP"}] and "--upstream=http://127.0.0.1:8000/" in ctr("chatwoot-mcp", "kube-rbac-proxy")["args"]'
check "mcp: token from its own Secret" 'secret("chatwoot-mcp", "mcp-token") == "mt" and [e["valueFrom"]["secretKeyRef"] for e in ctr("chatwoot-mcp", "server")["env"] if e["name"] == "CHATWOOT_API_TOKEN"] == [{"name": "chatwoot-mcp", "key": "mcp-token"}] and not any("chatwoot-mcp" in refs(pod(n)) for n in ("chatwoot-web", "chatwoot-worker", "chatwoot-prepare-1"))'
check "mcp: restricted" 'all(c["securityContext"] == {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}} for c in ctrs(pod("chatwoot-mcp"))) and pod("chatwoot-mcp")["securityContext"] == {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}} and all(c["resources"]["requests"].get("cpu") and c["resources"]["limits"].get("memory") for c in ctrs(pod("chatwoot-mcp")))'
check "mcp: proxy reviews as chatwoot-mcp" 'pod("chatwoot-mcp")["serviceAccountName"] == "chatwoot-mcp" and obj("ServiceAccount", "chatwoot-mcp") and "name: chatwoot-mcp" in obj("ConfigMap", "chatwoot-mcp-rbac")["data"]["config.yaml"] and "subresource: proxy" in obj("ConfigMap", "chatwoot-mcp-rbac")["data"]["config.yaml"]'
check "mcp: callers may proxy to this Service only" 'obj("Role", "chatwoot-mcp-caller")["rules"] == [{"apiGroups": [""], "resources": ["services/proxy"], "resourceNames": ["chatwoot-mcp"], "verbs": ["get", "create", "delete"]}] and obj("RoleBinding", "chatwoot-mcp-caller")["subjects"] == [{"kind": "ServiceAccount", "name": "gw", "namespace": "gateway"}]'
check "mcp: no caller account, no route by default" 'not obj("ServiceAccount", "chatwoot-mcp-caller") and not obj("Secret", "chatwoot-mcp-caller-token") and not obj("Route", "chatwoot-mcp")'
check "mcp: no cluster-scoped kind" 'not [d for d in docs if d["kind"] in ("ClusterRole", "ClusterRoleBinding", "Namespace")]'
check "mcp: service for a gateway" 'obj("Service", "chatwoot-mcp")["spec"]["ports"] == [{"name": "mcp", "port": 8443, "targetPort": "mcp", "protocol": "TCP", "appProtocol": "agentgateway.dev/mcp"}] and obj("Service", "chatwoot-mcp")["metadata"]["annotations"] == {"service.beta.openshift.io/serving-cert-secret-name": "chatwoot-mcp-tls", "agentgateway.dev/mcp-path": "/mcp", "agentgateway.dev/mcp-target-name": "chatwoot"} and "chatwoot-mcp-tls" in refs(pod("chatwoot-mcp"))'
check "mcp: web admits the server" '{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-mcp"}}} in obj("NetworkPolicy", "chatwoot-web")["spec"]["ingress"][0]["from"]'
check "mcp: callers' namespace reaches the proxy only" 'obj("NetworkPolicy", "chatwoot-mcp")["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8443}], "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "gateway"}}}]}] and obj("NetworkPolicy", "chatwoot-mcp")["spec"]["policyTypes"] == ["Ingress", "Egress"]'
check "mcp: server readiness on its loopback port" '"127.0.0.1" in ctr("chatwoot-mcp", "server")["readinessProbe"]["exec"]["command"][2] and "8000" in ctr("chatwoot-mcp", "server")["readinessProbe"]["exec"]["command"][2]'
check "mcp: egress to web, dns and the api" 'obj("NetworkPolicy", "chatwoot-mcp")["spec"]["egress"][0]["to"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "chatwoot-web"}}}] and obj("NetworkPolicy", "chatwoot-mcp")["spec"]["egress"][2] == {"ports": [{"protocol": "TCP", "port": 6443}]}'
fails "mcp: no host answered" "answers no Host header" "${MCP[@]}" --set mcp.callerAccount.enabled=true
OUT=$(render "${MCP[@]}" --set mcp.callerAccount.enabled=true --set "mcp.allowedHosts={gw.example.com}" --set 'mcp.ingressFrom[0].podSelector.matchLabels.app=gw')
check "mcp: no way in but the named peers" 'obj("NetworkPolicy", "chatwoot-mcp")["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8443}], "from": [{"podSelector": {"matchLabels": {"app": "gw"}}}]}]'
fails "mcp: generated route host needs allowedHosts" "needs the host OpenShift assigns in mcp.allowedHosts" "${MCP[@]}" --set mcp.callerAccount.enabled=true --set mcp.route.enabled=true
OUT=$(render "${MCP[@]}" --set mcp.callerAccount.enabled=true --set mcp.route.enabled=true --set mcp.route.hostname=mcp.example.com --set "mcp.allowedHosts={gw.example.com}" --set mcp.organization=Acme --set 'mcp.route.annotations.haproxy\.router\.openshift\.io/timeout=600s')
check "mcp: route timeout may be raised" 'obj("Route", "chatwoot-mcp")["metadata"]["annotations"] == {"haproxy.router.openshift.io/timeout": "600s"}'
check "mcp: organization" 'env("chatwoot-mcp", "server")["MCP_ORGANIZATION"] == "Acme"'
check "mcp: allowed hosts from route and values, no Service without in-cluster callers" 'env("chatwoot-mcp", "server")["MCP_ALLOWED_HOSTS"] == "mcp.example.com,mcp.example.com:*,gw.example.com,gw.example.com:*" and obj("Route", "chatwoot-mcp")["spec"]["host"] == "mcp.example.com"'
OUT=$(render "${MCP[@]}" --set mcp.callerAccount.enabled=true --set mcp.route.enabled=true --set "mcp.allowedHosts={chatwoot-mcp-test.apps.example.com}" --set networkPolicy.apiServerEgress.to[0].ipBlock.cidr=192.0.2.1/32)
check "mcp: caller account with its token" 'obj("ServiceAccount", "chatwoot-mcp-caller") and obj("Secret", "chatwoot-mcp-caller-token")["type"] == "kubernetes.io/service-account-token" and obj("Secret", "chatwoot-mcp-caller-token")["metadata"]["annotations"] == {"kubernetes.io/service-account.name": "chatwoot-mcp-caller"} and obj("RoleBinding", "chatwoot-mcp-caller")["subjects"] == [{"kind": "ServiceAccount", "name": "chatwoot-mcp-caller", "namespace": "test"}]'
check "mcp: route timeout for long tool calls" 'obj("Route", "chatwoot-mcp")["metadata"]["annotations"] == {"haproxy.router.openshift.io/timeout": "330s"}'
check "mcp: route reencrypts to the proxy" 'obj("Route", "chatwoot-mcp")["spec"]["tls"] == {"termination": "reencrypt", "insecureEdgeTerminationPolicy": "Redirect"} and obj("Route", "chatwoot-mcp")["spec"]["to"]["name"] == "chatwoot-mcp" and "host" not in obj("Route", "chatwoot-mcp")["spec"] and obj("Route", "chatwoot", )["spec"]["to"]["name"] == "chatwoot-web"'
check "mcp: routers reach the proxy" 'obj("NetworkPolicy", "chatwoot-mcp")["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8443}], "from": [{"namespaceSelector": {"matchLabels": {"policy-group.network.openshift.io/ingress": ""}}}]}]'
check "mcp: api egress to the named endpoint" 'obj("NetworkPolicy", "chatwoot-mcp")["spec"]["egress"][2]["to"] == [{"ipBlock": {"cidr": "192.0.2.1/32"}}]'
OUT=$(render "${MCP[@]}" --set mcp.callerAccount.enabled=true --set "mcp.allowedHosts={gw.example.com}" --set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault)
check "mcp: token from the store" '[(x["secretKey"], x["remoteRef"]["key"], x["remoteRef"]["property"]) for x in obj("ExternalSecret", "chatwoot-mcp")["spec"]["data"]] == [("mcp-token", "chatwoot-mcp-token", "login.password")] and not obj("Secret", "chatwoot-mcp")'

[ $fail = 0 ] && echo "PASS charts/chatwoot render" || exit 1
