#!/bin/bash
# Makes a Renovate update releasable: the chart's version goes up (patch for an upstream
# patch or digest, minor otherwise), appVersion follows when it named the old version, and
# release-notes/<chart>/<version>.md gets an entry. Renovate runs it after each update of a
# branch, on the working tree of that branch; HEAD is the base, so a second update in the
# same branch raises the version once and adds its own entry.
# usage: ci/renovate-release.sh <package file> <dependency> <old version> <new version> <update type>
set -euo pipefail
cd "$(dirname "$0")/.."
FILE=$1 DEP=$2 OLD=$3 NEW=$4 TYPE=$5
case "$FILE" in
  charts/*/*) C=$(echo "$FILE" | cut -d/ -f2) ;;
  *) exit 0 ;;
esac
CY="charts/$C/Chart.yaml"
[ -f "$CY" ] || exit 0

bump() {
  local IFS=. major minor patch
  read -r major minor patch <<<"$1"
  if [ "$2" = patch ]; then echo "$major.$minor.$((patch + 1))"; else echo "$major.$((minor + 1)).0"; fi
}
base=$(git show "HEAD:$CY" | awk '/^version:/{print $2}')
have=$(awk '/^version:/{print $2}' "$CY")
case "$TYPE" in patch|digest|pin|pinDigest) level=patch ;; *) level=minor ;; esac
want=$(bump "$base" "$level")
# the larger of what an earlier update of this branch set and what this one needs
target=$(printf '%s\n%s\n' "$have" "$want" | sort -V | tail -1)
sed -i "s/^version: .*/version: $target/" "$CY"

# appVersion that named the old upstream version follows it, keeping its v prefix
app=$(awk '/^appVersion:/{gsub(/"/, "", $2); print $2}' "$CY")
if [ -n "$app" ] && [ "${app#v}" = "${OLD#v}" ]; then
  case "$app" in v*) newapp="v${NEW#v}" ;; *) newapp=${NEW#v} ;; esac
  sed -i -E "s/^appVersion: .*/appVersion: $newapp/" "$CY"
fi

notes="release-notes/$C"
N="$notes/$target.md"
mkdir -p "$notes"
# a note this branch wrote for a lower version moves to the new one
for f in "$notes"/*.md; do
  [ -e "$f" ] && [ "$f" != "$N" ] || continue
  git ls-files --error-unmatch "$f" >/dev/null 2>&1 && continue
  [ -e "$N" ] || sed "1s/.*/# $C $target/" "$f" > "$N"
  rm -f "$f"
done
if [ ! -e "$N" ]; then
  if [ -f "charts/$C/templates/buildconfig.yaml" ]; then
    upgrade="The image is built again under a new tag; the pods move to it when the build is complete and restart once."
  else
    upgrade="The pods that run the new version restart once."
  fi
  printf '# %s %s\n\nReleased %s.\n\n## Changes\n\n## Upgrade\n\n%s Read the upstream release notes before an upgrade across a minor or major version.\n' \
    "$C" "$target" "$(date -u +%Y-%m-%d)" "$upgrade" > "$N"
fi
entry="### $DEP $NEW"
if ! grep -qxF "$entry" "$N"; then
  text="The chart moves \`$DEP\` from $OLD to $NEW (Renovate)."
  awk -v e="$entry" -v t="$text" '
    {print}
    /^## Changes$/ && !done {print ""; print e; print ""; print t; done=1}
  ' "$N" > "$N.tmp"
  mv "$N.tmp" "$N"
fi
