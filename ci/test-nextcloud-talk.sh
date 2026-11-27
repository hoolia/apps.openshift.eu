#!/bin/bash
# charts/nextcloud talk.shared: renders every Talk mode, refuses wrong values, and runs
# talk-shared.sh and the recorder start against stand-ins for php and the platform files.
# Needs helm and python3 with PyYAML.
set -u
cd "$(dirname "$0")/.."
C=charts/nextcloud
[ -d "$C/charts" ] || { echo "FAIL talk: run helm dependency build for $C first (ci/check.sh does)"; exit 1; }
fail=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
API="--api-versions security.openshift.io/v1 --api-versions route.openshift.io/v1 --api-versions monitoring.coreos.com/v1"
render() {
  local name=$1; shift
  helm template t "$C" --namespace test $API --set route.hostname=nc.example.org \
    --set monitoring.enabled=true "$@" > "$T/$name.yaml" 2> "$T/$name.err"
}

# mode: values, then the objects that must exist (1) or not (0)
check_mode() {
  local name=$1 want=$2; shift 2
  render "$name" "$@" || { echo "FAIL $name: render"; sed 's/^/    /' "$T/$name.err"; fail=1; return; }
  python3 - "$T/$name.yaml" "$want" "$name" <<'EOF' || fail=1
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
want = dict(kv.split("=") for kv in sys.argv[2].split())
mode = sys.argv[3]
has = lambda k, n: any(d["kind"] == k and d["metadata"]["name"] == n for d in docs)
get = lambda k, n: next(d for d in docs if d["kind"] == k and d["metadata"]["name"] == n)
setup = get("ConfigMap", "nextcloud-setup")["data"]
groups = [g["name"] for d in docs if d["kind"] == "PrometheusRule" for g in d["spec"]["groups"]]
rec = has("Deployment", "nextcloud-talk-recording")
rc = get("Deployment", "nextcloud-talk-recording")["spec"]["template"]["spec"]["containers"][0] if rec else {}
env = {e["name"] for e in rc.get("env", [])}
nc = get("Deployment", "nextcloud")["spec"]["template"]["spec"]
srcs = [s for v in nc["volumes"] if v["name"] == "setup-secrets" for s in v["projected"]["sources"]]
got = {
    "signaling": has("Deployment", "nextcloud-signaling"),
    "nats": has("Deployment", "nextcloud-nats"),
    "sigroute": has("Route", "talk-signaling"),
    "sigmonitor": has("ServiceMonitor", "nextcloud-signaling"),
    "sigalerts": "nextcloud-talk" in groups,
    "turn": has("Deployment", "nextcloud-turn"),
    "turnsvc": has("Service", "nextcloud-turn"),
    "talksecret": has("Secret", "nextcloud-talk"),
    "recording": rec,
    "recshared": "command" in rc and "HPB_DOMAIN" not in env,
    "reclocal": rec and "command" not in rc and "HPB_DOMAIN" in env,
    "recvolume": any(m["mountPath"] == "/recordings" for m in rc.get("volumeMounts", []))
                 and not any(m["mountPath"] == "/tmp" for m in rc.get("volumeMounts", [])),
    "localsig": "/talk/signaling/" in setup["setup.sh"],
    "localturn": "spreed turn_servers turn-secret" in setup["setup.sh"],
    "sharedsig": "signaling-url" in setup["talk-shared.sh"],
    "sharedturn": "turn-generation" in setup["talk-shared.sh"],
    "mount": any(s.get("configMap", {}).get("name") == "nextcloud-talk-platform" for s in srcs)
             and any(s.get("secret", {}).get("name") == "nextcloud-talk-shared-turn" for s in srcs),
}
bad = [f"{k}: want {want[k]}, got {int(got[k])}" for k in want if int(got[k]) != int(want[k])]
for b in bad:
    print(f"FAIL {mode}: {b}")
sys.exit(1 if bad else 0)
EOF
}

ON="--set talk.enabled=true"
G="--set talk.shared.turnGeneration=g1"
check_mode local-local \
  "signaling=1 nats=1 sigroute=1 sigmonitor=1 sigalerts=1 turn=1 turnsvc=1 talksecret=1 recording=1 reclocal=1 recshared=0 recvolume=1 localsig=1 localturn=1 sharedsig=0 sharedturn=0 mount=1" \
  $ON
check_mode sharedturn-localsig \
  "signaling=1 nats=1 sigroute=1 sigmonitor=1 sigalerts=1 turn=0 turnsvc=0 talksecret=1 reclocal=1 localsig=1 localturn=0 sharedsig=0 sharedturn=1" \
  $ON --set talk.shared.turn=true $G
check_mode shared-shared-recording \
  "signaling=0 nats=0 sigroute=0 sigmonitor=0 sigalerts=0 turn=0 turnsvc=0 talksecret=1 recording=1 recshared=1 reclocal=0 recvolume=1 localsig=0 localturn=0 sharedsig=1 sharedturn=1" \
  $ON --set talk.shared.signaling=true --set talk.shared.turn=true $G
check_mode shared-shared-norecording \
  "signaling=0 nats=0 sigroute=0 turn=0 talksecret=1 recording=0 localsig=0 localturn=0 sharedsig=1 sharedturn=1" \
  $ON --set talk.shared.signaling=true --set talk.shared.turn=true $G --set talk.recording.enabled=false
check_mode noturn-sharedsig \
  "signaling=0 nats=0 sigroute=0 turn=0 turnsvc=0 talksecret=1 recshared=1 localsig=0 localturn=0 sharedsig=1 sharedturn=0" \
  $ON --set talk.turn.enabled=false --set talk.shared.signaling=true
check_mode noturn-localsig \
  "signaling=1 nats=1 turn=0 turnsvc=0 localsig=1 localturn=0 sharedsig=0 sharedturn=0" \
  $ON --set talk.turn.enabled=false
check_mode talk-off \
  "signaling=0 nats=0 sigroute=0 sigmonitor=0 sigalerts=0 turn=0 talksecret=0 recording=0 localsig=0 localturn=0 sharedsig=0 sharedturn=0 mount=1"

# values the chart must refuse
refuse() {
  local name=$1 msg=$2; shift 2
  if render "$name" "$@"; then echo "FAIL refuse $name: rendered"; fail=1
  elif ! grep -q -- "$msg" "$T/$name.err"; then echo "FAIL refuse $name: no /$msg/ in:"; sed 's/^/    /' "$T/$name.err"; fail=1; fi
}
refuse turn-no-generation "talk.shared.turnGeneration" $ON --set talk.shared.turn=true
refuse generation-chars "turnGeneration" $ON --set talk.shared.turn=true --set 'talk.shared.turnGeneration=g 1'
refuse shared-talk-off "talk.shared needs talk.enabled" --set talk.shared.signaling=true
refuse string-switch "signaling" $ON --set-string talk.shared.signaling=true

# talk-shared.sh of a render against a stand-in php that logs its arguments
render script $ON --set talk.shared.signaling=true --set talk.shared.turn=true $G || { echo "FAIL script: render"; exit 1; }
render sigonly $ON --set talk.shared.signaling=true || { echo "FAIL sigonly: render"; exit 1; }
render nothing $ON || { echo "FAIL nothing: render"; exit 1; }
extract() {
  python3 - "$T/$1.yaml" "$2" "${3:-}" <<'EOF'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d:
        continue
    if sys.argv[2] in ("command", "args") and d["kind"] == "Deployment" and d["metadata"]["name"] == "nextcloud-talk-recording":
        sys.stdout.write(d["spec"]["template"]["spec"]["containers"][0][sys.argv[2]][2])
    if sys.argv[2] == "setup" and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "nextcloud-setup":
        sys.stdout.write(d["data"][sys.argv[3]])
EOF
}
mkdir -p "$T/bin" "$T/html/config" "$T/secrets/talk-platform"
cat > "$T/bin/php" <<EOF
#!/bin/sh
printf '%s\n' "\$*" >> "$T/php.log"
EOF
chmod +x "$T/bin/php"
for s in script sigonly nothing; do
  extract "$s" setup talk-shared.sh | sed -e "s#/run/secrets/setup#$T/secrets#g" -e "s#/var/www/html#$T/html#g" > "$T/$s.sh"
done
echo signaling > "$T/secrets/signaling-secret"
P=$T/secrets/talk-platform
platform() { rm -f "$P"/*; [ -n "$1" ] && printf '%s' "$1" > "$P/signaling-url"; [ -n "$2" ] && printf '%s' "$2" > "$P/turn-servers"
  [ -n "$3" ] && printf '%s' "$3" > "$P/turn-generation"; [ -n "$4" ] && printf '%s' "$4" > "$P/turn-secret"; true; }
# run <case> <script> <exit code>: OUT holds the log and the php calls
run() {
  rm -f "$T/php.log"; : > "$T/php.log"
  PATH="$T/bin:$PATH" sh "$T/$2.sh" > "$T/out" 2>&1; local rc=$?
  OUT="$(cat "$T/out")
$(cat "$T/php.log")"
  [ "$rc" = "$3" ] || { echo "FAIL $1: exit $rc, want $3:"; echo "$OUT" | sed 's/^/    /'; fail=1; }
}
expect() { echo "$OUT" | grep -qE -- "$2" || { echo "FAIL $1: expected /$2/ in:"; echo "$OUT" | sed 's/^/    /'; fail=1; }; }
reject() { echo "$OUT" | grep -qE -- "$2" && { echo "FAIL $1: unexpected /$2/ in:"; echo "$OUT" | sed 's/^/    /'; fail=1; }; }
marker() { if [ "$2" = 1 ]; then [ -e "$T/html/config/.chart-talk-failed" ] || { echo "FAIL $1: no failure marker"; fail=1; }
  else [ ! -e "$T/html/config/.chart-talk-failed" ] || { echo "FAIL $1: failure marker left"; fail=1; }; fi; }
URL=https://sig.example.test/backends/0f6c7c1e-1111-4222-8333-444455556666
KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCD

platform "" "" "" ""
run missing script 1
expect missing "no valid signaling-url"
expect missing "turn-generation is ''"
reject missing "config:app:set"
marker missing 1

platform "$URL" "t1.example.test:3478,t2.example.test:443" g1 "$KEY"
run good script 0
expect good "shared Talk servers set"
expect good "signaling-secret config:app:set spreed signaling_servers --value=\{\"servers\":\[\{\"server\":\"$URL\",\"verify\":true\}\],\"secret\":\"@SECRET@\"\}"
expect good "occ config:app:set spreed stun_servers --type=array -n --value=\[\"t1.example.test:3478\",\"t2.example.test:443\"\]$"
reject good "$KEY"
marker good 0
python3 - "$T/php.log" <<'EOF' || fail=1
import json, sys
for line in open(sys.argv[1]):
    if " turn_servers " in line:
        v = json.loads(line.split("--value=", 1)[1])
        want = [{"schemes": "turn", "server": s, "secret": "@SECRET@", "protocols": "udp,tcp"}
                for s in ("t1.example.test:3478", "t2.example.test:443")]
        if v != want:
            print(f"FAIL good: turn_servers {v}"); sys.exit(1)
        if "talk-platform/turn-secret" not in line:
            print("FAIL good: turn_servers not from the platform's key"); sys.exit(1)
        sys.exit(0)
print("FAIL good: turn_servers not set"); sys.exit(1)
EOF

platform "$URL" "t1.example.test:3478" g0 "$KEY"
run old-generation script 1
expect old-generation "turn-generation is 'g0', talk.shared.turnGeneration is 'g1'"
expect old-generation "spreed signaling_servers"
reject old-generation "turn_servers|stun_servers"
marker old-generation 1

platform "$URL" "t1.example.test:3478" g1 ""
run no-key script 1
expect no-key "no turn-secret"
reject no-key "turn_servers"

platform "$URL
https://evil.test/x" "t1.example.test:3478" g1 "$KEY"
run two-lines script 1
expect two-lines "no valid signaling-url"
reject two-lines "signaling_servers"

platform "http://sig.example.test/backends/x" "t1.example.test:3478\",\"x" g1 "$KEY"
run bad-values script 1
expect bad-values "no valid signaling-url"
expect bad-values "no valid turn-servers"
reject bad-values "config:app:set"

platform "$URL" "" "" ""
run sigonly sigonly 0
expect sigonly "spreed signaling_servers"
reject sigonly "turn_servers|turn-generation"
marker sigonly 0

: > "$T/html/config/.chart-talk-failed"
run nothing nothing 0
reject nothing "config:app:set"
marker nothing 0

# the recorder's start as Kubernetes runs it: command (shared signaling), then the image's
# start.sh, which writes recording.conf and runs the args
for s in script nothing; do extract "$s" args > "$T/$s-args.sh"; done
extract script command > "$T/rec.sh"
cat > "$T/start.sh" <<EOF
#!/bin/sh
echo "HPB \$HPB_DOMAIN \$HPB_PATH"
mkdir -p "$T/conf"; printf '[backend]\ndirectory = /tmp\n\n[backend-1]\n' > "$T/conf/recording.conf"
exec "\$@"
EOF
cat > "$T/bin/python" <<EOF
#!/bin/sh
echo "PYTHON \$* \$(grep directory "$T/conf/recording.conf")"
EOF
chmod +x "$T/start.sh" "$T/bin/python"
sed -i.bak -e "s#/run/talk-platform#$T/rec#g" -e "s#exec /start.sh#exec $T/start.sh#" "$T/rec.sh"
sed -i.bak "s#/conf/recording.conf#$T/conf/recording.conf#g" "$T/script-args.sh" "$T/nothing-args.sh"
mkdir -p "$T/rec"
recorder() { rm -f "$T/rec/signaling-url"; [ -n "$2" ] && printf '%s' "$2" > "$T/rec/signaling-url"
  OUT=$(PATH="$T/bin:$PATH" sh -c "$(cat "$T/rec.sh")" recording /bin/sh -c "$(cat "$T/script-args.sh")" 2>&1); local rc=$?
  [ "$rc" = "$3" ] || { echo "FAIL recorder $1: exit $rc, want $3: $OUT"; fail=1; }; }
recorder good "$URL" 0
expect "recorder good" "^HPB sig.example.test /backends/0f6c7c1e-1111-4222-8333-444455556666$"
expect "recorder good" "^PYTHON -m nextcloud.talk.recording --config $T/conf/recording.conf directory = /recordings$"
recorder slash "$URL/" 0
expect "recorder slash" "^HPB sig.example.test /backends/0f6c7c1e-1111-4222-8333-444455556666$"
recorder missing "" 1
recorder no-path "https://sig.example.test" 1
recorder two-lines "$URL
[signaling-2]" 1
recorder http "http://sig.example.test/backends/x" 1
# local signaling: the image's start.sh runs the args directly
OUT=$(PATH="$T/bin:$PATH" "$T/start.sh" /bin/sh -c "$(cat "$T/nothing-args.sh")" 2>&1) || { echo "FAIL recorder local: $OUT"; fail=1; }
expect "recorder local" "directory = /recordings$"
# an image whose recording.conf lacks the line must stop the recorder, not record into /tmp
printf '[backend]\ndirectory = /var/rec\n' > "$T/conf/recording.conf"
OUT=$(PATH="$T/bin:$PATH" sh -c "$(cat "$T/nothing-args.sh")" 2>&1) && { echo "FAIL recorder changed image: started: $OUT"; fail=1; }
expect "recorder changed image" "has no directory = /tmp"

[ "$fail" = 0 ] && echo "PASS talk" || exit 1
