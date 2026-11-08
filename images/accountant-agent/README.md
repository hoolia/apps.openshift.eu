# accountant-agent

An A2A agent for bookkeeping, built on the official `a2a-sdk` (A2A v0.3, JSON-RPC over
HTTP, the protocol kagent 0.x speaks to a BYO agent). Chart `charts/accountant-agent`
deploys it. Image: `ghcr.io/<owner>/accountant-agent:<VERSION>`, built and pushed by the
workflow `images.yaml` when `VERSION` changes on `main`.

## Behaviour

- One process serves every enabled role on its own port, each with its own agent card at
  `/.well-known/agent-card.json` and `/.well-known/agent.json`, the JSON-RPC endpoint at
  `/`, and `/readyz` (503 while the task store database does not answer).
- A role is a name, a port, a description, instructions and an optional list of tool
  name patterns (`fnmatch`, on `<server>__<tool>`). The chart ships `bookkeeper` (daily)
  and `cfo` (weekly).
- The model is any OpenAI-compatible chat completions endpoint (`model.baseUrl`). The
  bearer token is read from `model.tokenFile` on every call (a projected ServiceAccount
  token, renewed by the kubelet) or from the environment variable `model.apiKeyEnv`.
- Tools are listed from every MCP server in `mcpServers` (Streamable HTTP), again after
  `toolCacheSeconds`. A server that does not answer is left out of that round. A server
  entry with `tokenFile` gets that file as bearer token.
- Tasks live in memory, or in a database when the environment variable named by
  `taskStoreUrlEnv` (default `TASK_STORE_URL`) holds a URL: a CloudNativePG `uri`
  (`postgresql://...`) is used through asyncpg, table `a2a_tasks`.
- The transport sits behind `transport.Transport`; a gRPC transport wraps the same
  request handler.

## Approvals

The agent books nothing on its own. A run stops with task state `input-required` when

- a tool result carries `approvalRequired: true` and `draftId` (in `structuredContent`,
  or as a JSON object in the first text content), with optional `summary` and `tier`, or
- the model calls the built-in tool `request_approval` with `draftId` and `summary`.

The status message holds a short text per draft with the two answers, and one data part
per draft (`{"type": "approval", "draftId", "summary", "tier", "tool"}`):

    Approval needed.
    Draft D-1: Supplier A, 121.00 EUR, 4100 office
      boek D-1  |  afkeuren D-1 <reason>

The command words are `approval.approveCommand` and `approval.rejectCommand`. Whatever
front-end relays the task to a person sends the answer back as a new message on the same
task; the model passes it to the MCP tool that commits or discards the draft, and that
server checks the approval. The agent does not interpret the answer.

## Commands

- `python3 -m accountant_agent.server`: the agent (default command).
- `python3 -m accountant_agent.trigger <A2A URL> <text>`: posts one non-blocking task; the
  CronJob of a role schedule.
- `python3 -m accountant_agent.relay`: forwards `:8080` to `UPSTREAM`; the pod kagent runs
  for a BYO Agent.

## Test

    pip install -r requirements-test.txt
    pytest
