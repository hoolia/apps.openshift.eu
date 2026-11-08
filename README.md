# GRN.CLOUD application marketplace

Helm charts that tenants of the GRN.CLOUD OpenShift platform install into their own
projects.

    helm repo add grncloud https://apps.openshift.eu
    helm search repo grncloud

How to install and configure each chart, and the labels and annotations that change how
the applications work together, are published at https://docs.openshift.eu, built from
this repository by `mkdocs.yml` (`mkdocs build --strict`).

## What a chart may assume

The charts target the GRN.CLOUD platform. They rely on what the platform provides:
OpenShift Routes and builds, cert-manager, External Secrets, the MariaDB and
CloudNativePG operators, and object storage through ObjectBucketClaim. They are not
portable to an arbitrary Kubernetes cluster.

## Rules for a chart

- It installs into the release namespace and creates no Namespace and no cluster-scoped
  object.
- It ships no operator, no CRD and no SecurityContextConstraints binding.
- Every value has a default; a chart installs with no values given.
- `secrets.type` selects `kubernetes` (default) or `externalSecret`. With `kubernetes`,
  a machine-to-machine secret left empty is generated once and kept on upgrade.
- `README.md` has the sections Purpose, Install, How to login and Delivery instructions.
- Every object carries the `app` and `app.kubernetes.io` labels.
- `ci/check.sh` passes.

The full rules for a pull request are in [CONTRIBUTING.md](CONTRIBUTING.md).

A chart is released when the `version` in its `Chart.yaml` changes on `main`: the release
workflow runs `ci/release.sh <chart>`, which a person can also run by hand (it needs
`gitleaks` and `syft`). Each GitHub Release carries the package, the release notes from
`release-notes/<chart>/<version>.md` and an SBOM of the chart's images.

Renovate (workflow `renovate.yaml`, every morning and on demand) opens a pull request for
every new upstream version of an image or a chart dependency, one per chart and upstream.
`ci/renovate-release.sh` makes it releasable in the same pull request: the chart's
`version` goes up (patch for an upstream patch or digest, minor otherwise), `appVersion`
follows when it named the old version, and the release notes get an entry. Merging it
releases the chart. A major upgrade waits for approval on the Renovate dashboard issue.
The workflow needs the repository secrets `RENOVATE_APP_ID` and `RENOVATE_APP_PRIVATE_KEY`
of a GitHub App with read and write access to contents, pull requests, issues and
workflows.
## Skills and MCP servers

This repository also hosts agent skills and MCP servers as packages of
[APM](https://github.com/microsoft/apm) (`pip install apm-cli==0.35.0`):

| Directory | Package |
|---|---|
| `skills/<name>/SKILL.md` | one skill: `openshift-cli`, `gitlab-cli`, `helm-kustomize`, and the Exact Online accounting skills (`exact-*`, `nl-conventions` and the skills that name it) |
| `evals/accounting-nl/` | the evaluation set format, synthetic cases and runner of the accounting skills |
| `mcp/<server>/apm.yml` | one MCP server: `kubernetes` (read-only `kubernetes-mcp-server`) |
| `apm.yml` | every package above, for an agent session in a clone of this repository |
| `images/coding-agent/` | the Claude Code and OpenCode images, which carry these packages in the user's home |

Install a package into your own environment, for Claude Code and OpenCode at user scope:

    apm install -g --target claude,opencode hoolia/apps.openshift.eu/skills/gitlab-cli#<tag or commit>
    apm install -g --target claude,opencode --trust-transitive-mcp hoolia/apps.openshift.eu/mcp/kubernetes#<tag or commit>

In a clone of this repository, `apm install` writes all of them to `.claude/`, `.agents/`,
`.mcp.json` and `opencode.json`, which git ignores. A skill has front matter with `name`
equal to its directory and a `description` that starts with "Use when"; `ci/check.sh`
checks both, and that `apm.yml` and `images/coding-agent/apm.yml` list every package.
