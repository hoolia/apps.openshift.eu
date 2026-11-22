#!/bin/sh
set -eu

mkdir -p codex-tooling
while read -r binary version checksum; do
  asset=$binary-x86_64-unknown-linux-musl
  curl --fail --silent --show-error --location --max-time 120 \
    "https://github.com/openai/codex/releases/download/$version/$asset.tar.gz" \
    --output "codex-tooling/$binary.tar.gz"
  printf '%s  %s\n' "$checksum" "codex-tooling/$binary.tar.gz" | sha256sum --check
  tar -xzf "codex-tooling/$binary.tar.gz" -C codex-tooling
  mv "codex-tooling/$asset" "codex-tooling/$binary"
  rm "codex-tooling/$binary.tar.gz"
done <<'RELEASES'
codex rust-v0.167.0 9d64836106d5b374e90ee4454c7ebcde7ca7106558bd7e41a34b9f26ebda251a
codex-code-mode-host rust-v0.167.0 b5074d5dd64643b2c073927e494a588eff31c1f5a27f12c1e2c45f9a6cf07dd4
RELEASES
CODEX_HOME="$PWD/codex-tooling" codex-tooling/codex --version
CODEX_RUNTIME_BINARY="$PWD/codex-tooling/codex" node --test scripts/codex-runtime.test.mjs
rm codex-tooling/codex-code-mode-host
