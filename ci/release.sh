#!/bin/bash
# Releases one chart by hand: GitHub Release with the package, index.yaml on gh-pages.
# Produces what the release workflow produces; use it while the workflow cannot run.
# usage: ci/release.sh <chart>
set -eu
cd "$(dirname "$0")/.."
C=${1:?chart name}
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
git checkout -q main && git pull -q --ff-only origin main
V=$(awk '/^version:/{print $2}' "charts/$C/Chart.yaml"); TAG="$C-$V"
ci/check.sh | tail -1
P=$(mktemp -d)
helm package "charts/$C" -d "$P" >/dev/null
if gh release view "$TAG" >/dev/null 2>&1; then
  echo "release $TAG exists"
else
  gh release create "$TAG" "$P/$C-$V.tgz" --target main --title "$TAG" \
    --notes "$(awk -F': ' '/^description:/{print $2}' "charts/$C/Chart.yaml")" >/dev/null
  echo "release $TAG created"
fi
git checkout -q gh-pages && git pull -q --ff-only origin gh-pages
if [ -f index.yaml ]; then
  helm repo index "$P" --url "https://github.com/$REPO/releases/download/$TAG" --merge index.yaml
else
  helm repo index "$P" --url "https://github.com/$REPO/releases/download/$TAG"
fi
cp "$P/index.yaml" index.yaml
if git diff --quiet index.yaml; then
  echo "index.yaml unchanged"
else
  git add index.yaml && git commit -q -m "Update index.yaml for $TAG" && git push -q origin gh-pages
  echo "index.yaml published"
fi
git checkout -q main
rm -rf "$P"
