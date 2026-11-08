#!/bin/bash
# Render assertions for charts/hermes: approval signing key, private skill repositories
set -u
cd "$(dirname "$0")/.."
C=charts/hermes
fail=0
render() { helm template t "$C" --namespace test "$@" 2>&1; }
# check <label> <python expression>; ctr(name) one container, envs(c) its env names
check() {
  echo "$OUT" | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind")]
pod = [d for d in docs if d["kind"] == "StatefulSet"][0]["spec"]["template"]["spec"]
all_ctrs = pod["containers"] + pod.get("initContainers", [])
def ctr(name): return [c for c in all_ctrs if c["name"] == name][0]
def envs(c): return [e["name"] for e in c.get("env", [])]
def ref(c, var): return [e["valueFrom"]["secretKeyRef"] for e in c.get("env", []) if e["name"] == var][0]
sys.exit(0 if ('"$2"') else 1)
' 2>/dev/null || { echo "FAIL $1: $2"; fail=1; }
}

OUT=$(render --set a2a.enabled=true --set gateway.upstream=https://api.example.com --set fileServer.enabled=true --set fileServer.volume=dl --set 'extraVolumes[0].name=dl' --set 'extraVolumes[0].emptyDir.medium=')
check "no key by default" 'not any("APPROVAL_SIGNING_KEY" in envs(c) for c in all_ctrs)'

OUT=$(render --set approvalSigning.secretName=hermes-approval --set a2a.enabled=true --set gateway.upstream=https://api.example.com --set fileServer.enabled=true --set fileServer.volume=dl --set 'extraVolumes[0].name=dl' --set 'extraVolumes[0].emptyDir.medium=')
check "key in gateway" 'ref(ctr("gateway"), "APPROVAL_SIGNING_KEY") == {"name": "hermes-approval", "key": "approval-signing-key"}'
check "key in gateway only" '[c["name"] for c in all_ctrs if "APPROVAL_SIGNING_KEY" in envs(c)] == ["gateway"]'
check "no plain value" 'all("value" not in e for e in ctr("gateway")["env"] if e["name"] == "APPROVAL_SIGNING_KEY")'

OUT=$(render --set approvalSigning.secretName=s --set approvalSigning.key=signing)
check "own key" 'ref(ctr("gateway"), "APPROVAL_SIGNING_KEY") == {"name": "s", "key": "signing"}'

R=(--set 'skills.repositories[0].name=private' --set 'skills.repositories[0].url=git@github.com:example/private.git'
   --set 'skills.repositories[0].path=skills' --set 'skills.repositories[0].secretName=repo-private'
   --set 'skills.repositories[1].name=public' --set 'skills.repositories[1].url=https://github.com/example/public')
OUT=$(render "${R[@]}" --set gateway.upstream=https://api.example.com --set a2a.enabled=true)
check "auth mounted in seed-config" '[m["mountPath"] for m in ctr("seed-config")["volumeMounts"] if m["name"].startswith("skills-auth")] == ["/run/skills-auth", "/etc/hermes-skill-repos/private"]'
check "auth mounted nowhere else" 'not any(m["name"].startswith("skills-auth") for c in all_ctrs if c["name"] != "seed-config" for m in c.get("volumeMounts", []))'
check "auth volume" '[v for v in pod["volumes"] if v["name"] == "skills-auth-private"][0]["secret"] == {"secretName": "repo-private", "defaultMode": 0o400, "optional": True}'
check "auth scratch in memory" '[v for v in pod["volumes"] if v["name"] == "skills-auth-tmp"][0]["emptyDir"]["medium"] == "Memory"'
check "repositories env" '__import__("json").loads([e["value"] for e in ctr("seed-config")["env"] if e["name"] == "SKILL_REPOSITORIES"][0]) == [{"name": "private", "url": "git@github.com:example/private.git", "path": "skills", "authDir": "/etc/hermes-skill-repos/private"}, {"name": "public", "url": "https://github.com/example/public"}]'
check "github known hosts" '[e["value"] for e in ctr("seed-config")["env"] if e["name"] == "KNOWN_HOSTS"][0].count("github.com ") == 3'
OUT=$(render --set 'skills.repositories[0].name=public' --set 'skills.repositories[0].url=https://github.com/example/public')
check "no auth without secretName" 'not any(v["name"].startswith("skills-auth") for v in pod["volumes"]) and "KNOWN_HOSTS" not in envs(ctr("seed-config"))'

# both Secrets from the vault, as the cookie secret
ES=(--set secrets.type=externalSecret --set secrets.externalSecret.storeName=vault-test)
VAULT=(--set secrets.externalSecret.items.approvalSigningKey=item-approval
  --set 'skills.repositories[0].name=private' --set 'skills.repositories[0].url=git@github.com:example/private.git'
  --set 'skills.repositories[0].path=skills' --set 'skills.repositories[0].externalSecret=item-deploy-key'
  --set 'approval.approvers={whatsapp:+31600000000,tui:sam}' --set approval.webChatUser=sam)
OUT=$(render "${ES[@]}" "${VAULT[@]}")
check "approval ES" '[d for d in docs if d["kind"] == "ExternalSecret" and d["metadata"]["name"] == "t-approval-signing"][0]["spec"]["data"] == [{"secretKey": "approval-signing-key", "remoteRef": {"key": "item-approval", "property": "login.password", "conversionStrategy": "Default", "decodingStrategy": "None", "metadataPolicy": "None", "nullBytePolicy": "Ignore"}}]'
check "approval ES wired" 'ref(ctr("gateway"), "APPROVAL_SIGNING_KEY") == {"name": "t-approval-signing", "key": "approval-signing-key"}'
check "skills ES" '(lambda s: s["data"][0]["remoteRef"]["key"] == "item-deploy-key" and s["data"][0]["remoteRef"]["property"] == "notes" and s["data"][0]["secretKey"] == "sshPrivateKey" and s["target"]["name"] == "t-skills-private" and s["target"]["template"]["data"] == {"type": "git", "url": "git@github.com:example/private.git", "sshPrivateKey": "{{ .sshPrivateKey }}"})([d for d in docs if d["kind"] == "ExternalSecret" and d["metadata"]["name"] == "t-skills-private"][0]["spec"])'
check "skills ES wired" '[v for v in pod["volumes"] if v["name"] == "skills-auth-private"][0]["secret"]["secretName"] == "t-skills-private" and "authDir" in [e["value"] for e in ctr("seed-config")["env"] if e["name"] == "SKILL_REPOSITORIES"][0] and "externalSecret" not in [e["value"] for e in ctr("seed-config")["env"] if e["name"] == "SKILL_REPOSITORIES"][0]'
check "approvers env" '{e["name"]: e.get("value") for e in ctr("gateway")["env"]}["APPROVAL_APPROVERS"] == "whatsapp:+31600000000,tui:sam" and {e["name"]: e.get("value") for e in ctr("gateway")["env"]}["APPROVAL_WEB_CHAT_USER"] == "sam"'
check "approvers gateway only" '[c["name"] for c in all_ctrs if "APPROVAL_APPROVERS" in envs(c)] == ["gateway"]'
OUT=$(render "${ES[@]}")
check "no extra ES by default" '[d["metadata"]["name"] for d in docs if d["kind"] == "ExternalSecret"] == [] and not any(e.startswith("APPROVAL") for e in envs(ctr("gateway")))'
OUT=$(render "${ES[@]}" --set secrets.externalSecret.items.cookieSecret=item-cookie --set secrets.externalSecret.items.approvalSigningKey=item-approval --set approvalSigning.secretName=own)
check "own secret wins" 'ref(ctr("gateway"), "APPROVAL_SIGNING_KEY")["name"] == "own" and [d["metadata"]["name"] for d in docs if d["kind"] == "ExternalSecret"] == ["t-cookie-secret"]'
OUT=$(render --set secrets.externalSecret.items.approvalSigningKey=item-approval)
check "kubernetes type ignores vault item" 'not any("APPROVAL_SIGNING_KEY" in envs(c) for c in all_ctrs)'
render --set 'skills.repositories[0].name=p' --set 'skills.repositories[0].url=git@x:y' --set 'skills.repositories[0].externalSecret=i' >/dev/null && { echo "FAIL skills externalSecret without secrets.type externalSecret accepted"; fail=1; }
render --set 'approval.approvers={sam}' >/dev/null && { echo "FAIL schema: approver without platform accepted"; fail=1; }

render --set approvalSigning.secretName=Bad_Name >/dev/null && { echo "FAIL schema: Bad_Name accepted"; fail=1; }
render --set approvalSigning.other=x >/dev/null && { echo "FAIL schema: unknown key accepted"; fail=1; }

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
