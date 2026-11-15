# oh-my-openagent

## Purpose

Runs one personal coding agent for one person in your project: OpenCode with the
[oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (OmO) agent harness
loaded. OmO adds an orchestrating main agent, planning and review agents, background
subagents per task category, the `ultrawork` (`ulw`) keyword and a skill set to OpenCode.
The person works with it in the browser behind a login; other agents call it over A2A
(agent-to-agent, port 8080).

The chart is chart [opencode-agent](../opencode-agent) with OmO switched on: every value
of opencode-agent is a value of this chart under `agent`. This chart sets three of them:

- `agent.opencode.plugins` (default: `[oh-my-openagent@5.1.70]`): OpenCode installs OmO
  from npm on start. A list given at install replaces this one, so keep
  `oh-my-openagent@<version>` in it; the schema refuses a list without it.
- `agent.opencode.env` (default: `OMO_SEND_ANONYMOUS_TELEMETRY=0`, `OMO_DISABLE_POSTHOG=1`):
  OmO's anonymous telemetry is off.
- `agent.opencode.homeFiles.".omo/omo.jsonc"` (default: telemetry and auto update off):
  OmO's user configuration, written to `~/.omo/omo.jsonc` on every start. Give OmO's agents
  and task categories the models of your gateway under `"[opencode]"`, as
  `"<provider>/<model>"` (see OmO's
  [configuration reference](https://github.com/code-yeongyu/oh-my-openagent/blob/v5.1.70/docs/reference/configuration.md)).
  A map given at install is merged over the default. A repository in the work tree can add
  its own `.omo/omo.jsonc`, which wins over this file.

Without a model per agent and category, OmO uses its built-in chains of Anthropic, OpenAI,
Kimi and other vendor models, and falls back to OpenCode's default model when the gateway
serves none of them.

## Install

From the Developer Catalog, or with Helm:

    helm repo add apps https://apps.openshift.eu
    helm install sam apps/oh-my-openagent \
      --set agent.person.name=sam \
      --set agent.person.email=sam@example.com \
      --set agent.gateway.upstream=https://gateway.example.com \
      --set agent.route.host=sam.apps.example.com

Values for the agent itself (`agent.person`, `agent.route`, `agent.oauth`, `agent.secrets`,
`agent.gateway`, `agent.a2a`, `agent.git`, `agent.mcp`, `agent.storage` and the rest) are
described in the README of chart opencode-agent.

An example OmO configuration for a gateway whose OpenCode provider is `gateway`:

    agent:
      opencode:
        homeFiles:
          .omo/omo.jsonc:
            "[opencode]":
              agents:
                explore: {model: gateway/claude-haiku-4-5}
                librarian: {model: gateway/claude-haiku-4-5}
                plan-consultant: {model: gateway/claude-opus-5-5}
                plan-reviewer: {model: gateway/claude-opus-5-5}
              categories:
                quick: {model: gateway/claude-haiku-4-5}
                unspecified-low: {model: gateway/claude-sonnet-5}
                unspecified-high: {model: gateway/claude-opus-5-5}

## How to login

- **Single sign-on**: the OpenShift login of the cluster, through an OAuth proxy.
  `agent.oauth.ownerEmail` (default `agent.person.email`), `agent.oauth.allowedGroups` and
  `agent.oauth.loginClusterRole` decide who gets in.
- **Admin user**: OpenCode has one user, `opencode`, password in Secret
  `<person.name>-agent-server-password`, key `password`:

      oc -n <project> extract secret/<person.name>-agent-server-password --keys=password --to=-

## Delivery instructions

- Everything in the Delivery instructions of chart opencode-agent applies: kagent on the
  cluster, a ReadWriteMany volume, the gateway accepting ServiceAccount tokens of your
  project.
- The opencode container needs egress to the npm registry (registry.npmjs.org) on start:
  OpenCode installs `oh-my-openagent` and its platform binary from there.
- OmO is licensed under the Sustainable Use License 1.0, not an open source licence: use
  for your own internal business purposes, or non-commercial or personal use, is
  permitted. The chart holds no OmO code; the agent downloads OmO from npm, and you accept
  that licence for your own use. Check it before you use OmO in a service you offer to
  others.
- OmO's main agent runs on the model selected in the UI. Its delegated agents and
  categories run on the models in `~/.omo/omo.jsonc`; a model your gateway does not serve
  fails that task.
- `~/.omo/omo.jsonc` is rewritten on every start; change it through
  `agent.opencode.homeFiles`, or put settings in a repository's `.omo/omo.jsonc`. OmO's
  memory, sessions and migration state stay in `~/.omo` on the volume.
