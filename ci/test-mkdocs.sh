#!/bin/bash
# Render assertions for charts/mkdocs
set -u
cd "$(dirname "$0")/.."
C=charts/mkdocs
fail=0
render() { helm template mkdocs "$C" --namespace test "$@" 2>&1; }
has()   { echo "$OUT" | grep -qF -- "$2" || { echo "FAIL $1: missing: $2"; fail=1; }; }
hasnt() { echo "$OUT" | grep -qF -- "$2" && { echo "FAIL $1: unexpected: $2"; fail=1; }; }

OUT=$(render)
for k in BuildConfig ImageStream Deployment Service Route ConfigMap; do has default "kind: $k"; done
has default "mkdocs new /tmp/src"
has default "type: Dockerfile"
has default "targetPort: 8080"
has default "memory: 1Gi"
hasnt default "kind: CronJob"
hasnt default "kind: NetworkPolicy"
hasnt default "kind: Secret"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.contextDir=site --set source.requirementsFile=req.txt)
has git "uri: https://example.com/docs.git"
has git "contextDir: site"
has git "COPY --chown=1001:0 . /tmp/src"
has git "pip install --no-cache-dir -r req.txt"
has git "mkdocs build --strict -f mkdocs.yml"
hasnt git "sourceSecret"
hasnt git "mkdocs new"

OUT=$(render --set source.gitRepo=https://example.com/docs.git --set source.strict=false)
hasnt nostrict "--strict"

[ $fail = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
