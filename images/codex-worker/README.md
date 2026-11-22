# codex-worker

Image of the review worker that the `codex-worker` chart installs. One private HTTPS worker
runs Codex merge request reviews on a ChatGPT subscription, one at a time, and owns the
renewable session, so no CI job holds it.

## How it works

The pod runs two containers from this image.

| | `worker` | `codex` |
|---|---|---|
| Starts | the image entrypoint (`codex-worker-lock.py`, then `codex-worker.mjs`) | `node scripts/codex-exec-agent.mjs` |
| Listens | TLS on `CODEX_WORKER_PORT` (3443), bearer `CODEX_WORKER_TOKEN` | plain HTTP on `127.0.0.1:3444` |
| Holds | request token, TLS key, auth-store token, gateway key, the session on its volume | no Secret and no token variable |

- A CI job sends the prepared review text with `scripts/codex-worker-client.mjs` to
  `POST /reviews` and gets the review as JSON (`verdict` `OK` or `BLOCKING`, and `review`).
- For each review the worker writes a copy of the session and the CLI configuration into a
  request directory of the exchange volume and asks the agent in `codex` to run the Codex
  CLI on it. The CLI has no shell and no tools.
- The CLI calls a model gateway (`CODEX_GATEWAY_URL`) with the gateway key in `x-bf-vk` and
  the subscription token in `Authorization`. There is no direct route to the model backend.
- The worker takes a refreshed session back, keeps it in its private state and mirrors it
  to a variable of a separate GitLab project, the auth store.
- `GET /healthz` proves a running process only. `GET /metrics` serves
  `codex_worker_reviews_total`, `codex_worker_session_mirror_failures_total`,
  `codex_worker_pending_reviews` and `codex_worker_oldest_review_age_seconds`.

A review that has started always finishes, also when its client leaves. A request still in
the queue is dropped when its client has left.

## Build

The Containerfile starts from the same pinned `node:22-bookworm` as the Claude image of
`coding-agent` and installs the same tools with `images/coding-agent/install-tools.sh`, so
the build context is the repository root. It downloads the Codex CLI release pinned in
`scripts/prepare-codex.sh`, checks its sha256 and runs it once. It then runs the test
suites of the worker, the agent and the client; a failing test fails the build.

```bash
podman build -t codex-worker:$(cat images/codex-worker/VERSION) \
  -f images/codex-worker/Containerfile .
```

The workflow `.github/workflows/images.yaml` (job `codex-worker`) builds the image on a
pull request and pushes `ghcr.io/<owner>/codex-worker:<VERSION>` on a push to `main`. A tag
is never rebuilt: raise `VERSION` with every change of the image.

The tests need Node.js 22, Python 3 and `openssl`:

```bash
cd images/codex-worker
node --test scripts/*.test.mjs
```

## Use

Install the image with `charts/codex-worker`, which sets every variable below except the
credentials' values.

| Variable | Container | Meaning |
|---|---|---|
| `CODEX_WORKER_TOKEN` | `worker` | bearer a client must send |
| `CODEX_WORKER_TLS_CERT_FILE`, `CODEX_WORKER_TLS_KEY_FILE` | `worker` | serving certificate; required beyond loopback |
| `CI_API_V4_URL`, `CI_PROJECT_ID` | `worker` | GitLab API and the project that requests reviews |
| `CODEX_AUTH_STORE_PROJECT_ID`, `CODEX_AUTH_STORE_TOKEN` | `worker` | the separate auth-store project and its access token |
| `CODEX_GATEWAY_URL`, `CODEX_GATEWAY_KEY` | `worker` | HTTPS gateway route to the Codex backend and its key |
| `CODEX_AUTH_STATE_DIR` | `worker` | private state, mode 0700, on a volume only this container mounts |
| `CODEX_EXCHANGE_DIR` | both | request directories, on a volume both containers mount |
| `CODEX_AGENT_URL` | `worker` | the agent, `http://127.0.0.1:3444` |
| `CODEX_AGENT_PORT` | `codex` | port of the agent |

The auth store is variable `CODEX_REVIEW_AUTH_JSON_B64` of the auth-store project, scope
`codex-review`, protected, masked and not expanded: the base64 of the Codex `auth.json`
after `codex login --device-auth` with `cli_auth_credentials_store = "file"`. One worker
owns one session; a second worker on the same session invalidates it for both.
