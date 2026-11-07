# claude-agent

Image of a Claude agent: Claude Code, the Claude Agent SDK (TypeScript and Python) and
Codex on Node.js 22 (Debian bookworm), with the command-line tools of an OpenShift
platform. CI jobs, kAgent agents and other agent runtimes run it instead of installing
the agents at run time.

## Purpose

| Group | Tools |
|---|---|
| Agents | `claude` 2.1.310, `@anthropic-ai/claude-agent-sdk` 0.3.310, `claude-agent-sdk` 0.2.173 (Python, `/opt/venv`), `codex` 0.147.0 |
| Checks | `pytest`, `ruff`, `mkdocs` with Material, `promtool`, `kube-linter`, `hadolint`, `shellcheck`, `kubeconform`, `gitleaks` |
| Platform | `git`, `glab`, `gh`, `oc`, `kubectl`, `helm`, `kustomize`, `yq`, `jq` and the rest of the tools image |

The platform tools come from the build argument `TOOLS_IMAGE`: every binary in its
`/usr/local/bin` (except its own agent and Node.js) lands in `/opt/tools/bin`, and its
`/usr/local/go` in `/usr/local/go`. `checks.txt` lists one command per tool;
`verify-tools.sh` runs each as the image's user during the build and fails it on the first
missing tool.

The image runs as any UID in group 0. Its entrypoint adds that UID to `/etc/passwd`;
`HOME` is `/home/agent`, writable by group 0. `DISABLE_AUTOUPDATER` and
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` are set.

## Build

A BuildConfig with Docker strategy, context `images/claude-agent` of this repository at a
pinned commit, and the build argument:

```yaml
strategy:
  type: Docker
  dockerStrategy:
    dockerfilePath: Containerfile
    buildArgs:
    - name: TOOLS_IMAGE
      value: <registry>/<opencode-agent image>:<tag>
```

Tag the image `<claude code version>-<revision>` and raise the revision for every change
of this directory.

## Use

Point a model gateway at Claude Code with `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`
(plus `ANTHROPIC_CUSTOM_HEADERS` for a gateway that reads its key from another header),
then run `claude -p` in a checkout. In a GitLab CI job, set `image:` to the built tag.
