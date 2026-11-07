# coding-agent

Images of a coding agent, one per agent, with the skills and MCP servers of this repository
in the user's home. CI jobs, kAgent agents and other agent runtimes run them instead of
installing an agent at run time.

## Purpose

| Containerfile | Base | Agent | Skills and MCP servers |
|---|---|---|---|
| `Containerfile.claude` | `node:22-bookworm` | Claude Code 2.1.310, Claude Agent SDK 0.3.310 (npm) and 0.2.173 (Python, `/opt/venv`) | `~/.claude/skills/`, `~/.claude.json` |
| `Containerfile.opencode` | `ghcr.io/anomalyco/opencode:2.1.1` (Alpine) | OpenCode 2.1.1 | `~/.config/opencode/skills/`, `~/.config/opencode/opencode.json` |

Both carry the tools the skills describe, each pinned and checked against its published
sha256 (`install-tools.sh`): `git`, `glab`, `gh`, `oc`, `kubectl`, `helm`, `kustomize`,
`yq`, `jq`, `shellcheck`, `pytest`, `ruff`, `apm` and `kubernetes-mcp-server`.

`apm.yml` here is the user-scope APM manifest (`~/.apm/apm.yml`): it lists the packages
under `skills/` and `mcp/` of this repository, which the Containerfile copies to `/opt/apm`
and installs with `apm install -g --target <agent>`. Add a package there and to the root
`apm.yml`; `ci/check.sh` fails when either misses one.

`checks-common.txt` and `checks-<agent>.txt` list one command per tool and installed
skill; `verify-tools.sh` runs each as the image's user during the build and fails it when
one is missing.

The images run as any UID in group 0. The entrypoint adds that UID to `/etc/passwd`;
`HOME` is `/home/agent`, writable by group 0.

## Build

The workflow `.github/workflows/images.yaml` (job `coding-agent`) builds both images from
the repository root and pushes them to `ghcr.io/<owner>/coding-agent` on a push to `main`:

| Image | Tag |
|---|---|
| Claude Code | `claude-<VERSION.claude>`, for example `claude-2.1.310-1` |
| OpenCode | `opencode-<VERSION.opencode>`, for example `opencode-2.1.1-1` |

A tag is never rebuilt: raise the `VERSION.<agent>` file with every change of the image,
the skills or the MCP packages. A pull request builds both images without pushing; the
checks of `verify-tools.sh` run inside the build.

## Use

Point Claude Code at a model gateway with `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`
(plus `ANTHROPIC_CUSTOM_HEADERS` for a gateway that reads its key from another header),
then run `claude -p` in a checkout; OpenCode reads its provider from `opencode.json`. In a
CI job, set `image:` to `ghcr.io/<owner>/coding-agent:<tag>`.
