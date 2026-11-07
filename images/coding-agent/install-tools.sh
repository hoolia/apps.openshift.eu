#!/bin/sh
# The tools both coding agents use, pinned and checked against their published sha256,
# on Debian (apt) or Alpine (apk); the agent itself is installed by its Containerfile
set -eux

if command -v apk >/dev/null; then
  # glibc-only binaries (oc) run through gcompat
  apk add --no-cache bash ca-certificates curl git jq less tini python3 py3-pip shellcheck \
    gcompat libstdc++ nodejs npm tar gzip
else
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl git jq less tini python3-venv shellcheck
  rm -rf /var/lib/apt/lists/*
fi

work=$(mktemp -d)
fetch() {
  curl -fsSL -o "$work/$3" "$1"
  echo "$2  $work/$3" | sha256sum -c -
}
fetch https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.22.0/openshift-client-linux-4.22.0.tar.gz \
  b6e027b304678d55e89ea1e8fe5c89e832f9c1a73518a1846e8cf9601c647aef oc.tgz
tar -xzf "$work/oc.tgz" -C /usr/local/bin oc kubectl
fetch https://get.helm.sh/helm-v3.22.1-linux-amd64.tar.gz \
  896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea helm.tgz
tar -xzf "$work/helm.tgz" -C "$work" linux-amd64/helm && install -m 0755 "$work/linux-amd64/helm" /usr/local/bin/helm
fetch https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.3/kustomize_v5.8.3_linux_amd64.tar.gz \
  cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61 kustomize.tgz
tar -xzf "$work/kustomize.tgz" -C /usr/local/bin kustomize
fetch https://github.com/mikefarah/yq/releases/download/v4.54.1/yq_linux_amd64.tar.gz \
  e68a456f90c577af3fe4960184b3a3cf5c461e0348407c10f107da3a5fec8972 yq.tgz
tar -xzf "$work/yq.tgz" -C "$work" ./yq_linux_amd64 && install -m 0755 "$work/yq_linux_amd64" /usr/local/bin/yq
fetch https://gitlab.com/gitlab-org/cli/-/releases/v1.124.0/downloads/glab_1.124.0_linux_amd64.tar.gz \
  63df684842b0697e418649dd64374d37ac1da0fbcdcb422d6a5557222f8bb40f glab.tgz
tar -xzf "$work/glab.tgz" -C "$work" bin/glab && install -m 0755 "$work/bin/glab" /usr/local/bin/glab
fetch https://github.com/cli/cli/releases/download/v2.103.2/gh_2.103.2_linux_amd64.tar.gz \
  62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8 gh.tgz
tar -xzf "$work/gh.tgz" -C "$work" gh_2.103.2_linux_amd64/bin/gh && install -m 0755 "$work/gh_2.103.2_linux_amd64/bin/gh" /usr/local/bin/gh
rm -rf "$work"

# Python checks and APM, which installs the skills and MCP servers
python3 -m venv /opt/venv
/opt/venv/bin/pip install --no-cache-dir pytest==8.4.2 pyyaml==6.0.3 ruff==0.17.1 apm-cli==0.35.0

# the MCP server the kubernetes package of this repository starts
npm install -g --no-audit --no-fund kubernetes-mcp-server@0.0.69
npm cache clean --force

# arbitrary UID: the home and the passwd file belong to group 0
printf '%s\n' '#!/bin/sh' \
  'if ! whoami >/dev/null 2>&1; then' \
  '  echo "agent:x:$(id -u):0:agent:${HOME}:/bin/sh" >> /etc/passwd' \
  'fi' \
  'exec "$@"' > /usr/local/bin/agent-uid-entrypoint
chmod 0755 /usr/local/bin/agent-uid-entrypoint
mkdir -p /home/agent
chmod g=u /etc/passwd
