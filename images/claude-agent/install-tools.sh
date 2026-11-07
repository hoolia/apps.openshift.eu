#!/bin/sh
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends tini jq python3-venv ca-certificates less
rm -rf /var/lib/apt/lists/*

# agents: Claude Code, the Claude Agent SDK and Codex
npm install -g --no-audit --no-fund \
  @anthropic-ai/claude-code@2.1.310 \
  @anthropic-ai/claude-agent-sdk@0.3.310 \
  @openai/codex@0.147.0
npm cache clean --force

# Python: the Agent SDK and the checks a repository runs
python3 -m venv /opt/venv
/opt/venv/bin/pip install --no-cache-dir \
  claude-agent-sdk==0.2.173 \
  pytest==8.4.2 \
  pyyaml==6.0.3 \
  ruff==0.17.1 \
  mkdocs==1.6.1 \
  mkdocs-material==9.7.7

# arbitrary UID: the home and the passwd file belong to group 0
printf '%s\n' '#!/bin/sh' \
  'if ! whoami >/dev/null 2>&1; then' \
  '  echo "agent:x:$(id -u):0:agent:${HOME}:/bin/bash" >> /etc/passwd' \
  'fi' \
  'exec "$@"' > /usr/local/bin/agent-uid-entrypoint
chmod 0755 /usr/local/bin/agent-uid-entrypoint
mkdir -p /home/agent
chgrp -R 0 /home/agent
chmod -R g=u /home/agent
chmod g=u /etc/passwd
