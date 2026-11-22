import { spawn } from "node:child_process";
import { createHash, timingSafeEqual } from "node:crypto";
import { fstatSync } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { createServer as httpServer } from "node:http";
import { createServer as httpsServer } from "node:https";
import { isIP } from "node:net";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";

const limit = 8 * 1024 * 1024;
const runner = fileURLToPath(new URL("./codex-chatgpt.mjs", import.meta.url));
const digest = (value) => createHash("sha256").update(value).digest();
const loopback = (host) => host === "localhost" || host === "::1" ||
  (isIP(host) === 4 && host.startsWith("127."));

class WorkerError extends Error {
  constructor(kind, status = 500, exitCode = null) {
    super(kind);
    this.kind = kind;
    this.status = status;
    this.exitCode = exitCode;
  }
}

// the runner writes one fixed sentence without secret values when it fails
function diagnosis(child) {
  let text = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (data) => { text = (text + data).slice(-2000); });
  return () => text.split("\n").findLast((line) => /^Codex [ -~]{1,200}$/.test(line)) ?? null;
}

class OwnerLostError extends WorkerError {
  constructor() {
    super("execution_owner_lost", 503);
  }
}

async function execute(input, environment) {
  const directory = await mkdtemp(join(tmpdir(), "codex-worker-"));
  await chmod(directory, 0o700);
  let ownerLost = false;
  try {
    const output = join(directory, "review.json");
    const child = spawn(process.execPath, [runner, output], {
      cwd: directory,
      env: { ...environment, HOME: directory, TMPDIR: directory },
      stdio: ["pipe", "ignore", "pipe", 3],
    });
    const reason = diagnosis(child);
    const completion = new Promise((resolve, reject) => {
      child.once("error", () => reject(new WorkerError("execution_start_failed")));
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    child.stdin.on("error", (error) => {
      if (error.code !== "EPIPE" && error.code !== "ERR_STREAM_DESTROYED") {
        child.stdin.destroy();
      }
    });
    child.stdin.end(input);
    const { code: exitCode, signal } = await completion;
    if (signal) {
      ownerLost = true;
      throw new OwnerLostError();
    }
    // 3: the review is complete, the refreshed session is not mirrored to the store
    if (exitCode !== 0 && exitCode !== 3) {
      const failure = new WorkerError("execution_failed", 502, exitCode);
      failure.reason = reason();
      throw failure;
    }
    if ((await stat(output)).size > limit) throw new WorkerError("invalid_review", 502, 0);
    const raw = await readFile(output, "utf8");
    const review = JSON.parse(raw);
    if (!review || Array.isArray(review) || Object.keys(review).length !== 2 ||
        !["OK", "BLOCKING"].includes(review.verdict) ||
        typeof review.review !== "string" || !review.review.trim()) {
      throw new WorkerError("invalid_review", 502, 0);
    }
    return { raw, unmirrored: exitCode === 3 ? reason() ?? "unknown" : null };
  } finally {
    if (!ownerLost) await rm(directory, { recursive: true, force: true });
  }
}

async function main() {
  const host = process.env.CODEX_WORKER_HOST ?? "0.0.0.0";
  const port = Number(process.env.CODEX_WORKER_PORT ?? "3443");
  const token = process.env.CODEX_WORKER_TOKEN;
  const state = process.env.CODEX_AUTH_STATE_DIR;
  const cert = process.env.CODEX_WORKER_TLS_CERT_FILE;
  const key = process.env.CODEX_WORKER_TLS_KEY_FILE;
  if (!token || !state || !isAbsolute(state) || !Number.isInteger(port) ||
      port < 0 || port > 65535 || Boolean(cert) !== Boolean(key) ||
      (!loopback(host) && !cert)) {
    throw new WorkerError("invalid_worker_config");
  }
  const environment = Object.fromEntries([
    "PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
    "CI_API_V4_URL", "CI_PROJECT_ID", "CODEX_AUTH_STORE_PROJECT_ID",
    "CODEX_AUTH_STORE_TOKEN", "CODEX_AUTH_STATE_DIR", "CODEX_AUTH_LOCK_FD",
    "CODEX_EXCHANGE_DIR", "CODEX_AGENT_URL", "CODEX_AGENT_POLL_MS", "CODEX_AGENT_WAIT_MS",
    "CODEX_GATEWAY_URL", "CODEX_GATEWAY_KEY", "CODEX_MODEL",
  ].filter((name) => process.env[name] !== undefined).map((name) => [name, process.env[name]]));
  if (!["CI_API_V4_URL", "CI_PROJECT_ID", "CODEX_AUTH_STORE_PROJECT_ID",
    "CODEX_AUTH_STORE_TOKEN", "CODEX_EXCHANGE_DIR", "CODEX_AGENT_URL",
    "CODEX_GATEWAY_URL", "CODEX_GATEWAY_KEY", "CODEX_MODEL"].every((name) => environment[name])) {
    throw new WorkerError("invalid_worker_config");
  }
  await mkdir(state, { recursive: true, mode: 0o700 });
  const stateInfo = await stat(state);
  if (!stateInfo.isDirectory() || (stateInfo.mode & 0o777) !== 0o700) {
    throw new WorkerError("invalid_state_permissions");
  }
  if (environment.CODEX_AUTH_LOCK_FD !== "3") {
    throw new WorkerError("missing_worker_lease");
  }
  const lock = await lstat(join(state, "private", "owner.lock"));
  const inherited = fstatSync(3);
  if (!lock.isFile() || !inherited.isFile() || lock.dev !== inherited.dev ||
      lock.ino !== inherited.ino || lock.nlink !== 1 ||
      lock.uid !== process.getuid()) {
    throw new WorkerError("invalid_worker_lease");
  }
  // adopt the session of a review that an earlier worker did not finish
  const recovery = spawn(process.execPath, [runner, "--recover"], {
    env: environment, stdio: ["ignore", "ignore", "pipe", 3],
  });
  const reason = diagnosis(recovery);
  const recovered = await new Promise((resolve) => {
    recovery.once("error", () => resolve(1));
    recovery.once("close", (code) => resolve(code ?? 1));
  });
  if (recovered !== 0) {
    console.error(JSON.stringify({ error: "recovery_failed", reason: reason() }));
    throw new WorkerError("recovery_failed");
  }
  const expected = digest(`Bearer ${token}`);
  let queue = Promise.resolve();
  let accepting = true;
  let fenced = false;
  const acceptedAt = [];
  const completed = { success: 0, failure: 0, cancelled: 0 };
  let unmirrored = 0;
  function reply(response, status, body, exitCode = null) {
    if (response.destroyed) return;
    response.writeHead(status, {
      "content-type": "application/json",
      "cache-control": "no-store",
      ...(exitCode === null ? {} : { "x-codex-exit-code": String(exitCode) }),
    });
    response.end(body);
  }
  async function handle(request, response) {
    try {
      if (!accepting) throw new WorkerError("shutting_down", 503);
      if (request.method === "GET" && request.url === "/healthz") {
        reply(response, 200, '{"live":true}');
        return;
      }
      if (request.method === "GET" && request.url === "/metrics") {
        response.writeHead(200, {
          "content-type": "text/plain; version=0.0.4; charset=utf-8",
          "cache-control": "no-store",
        });
        response.end([
          "# TYPE codex_worker_reviews_total counter",
          `codex_worker_reviews_total{result="success"} ${completed.success}`,
          `codex_worker_reviews_total{result="failure"} ${completed.failure}`,
          `codex_worker_reviews_total{result="cancelled"} ${completed.cancelled}`,
          "# TYPE codex_worker_session_mirror_failures_total counter",
          `codex_worker_session_mirror_failures_total ${unmirrored}`,
          "# TYPE codex_worker_pending_reviews gauge",
          `codex_worker_pending_reviews ${acceptedAt.length}`,
          "# TYPE codex_worker_oldest_review_age_seconds gauge",
          `codex_worker_oldest_review_age_seconds ${acceptedAt.length
            ? (performance.now() - acceptedAt[0]) / 1000 : 0}`,
          "",
        ].join("\n"));
        return;
      }
      if (request.method !== "POST" || request.url !== "/reviews") {
        throw new WorkerError("not_found", 404);
      }
      if (!timingSafeEqual(expected, digest(request.headers.authorization ?? ""))) {
        throw new WorkerError("unauthorized", 401);
      }
      let size = 0;
      const chunks = [];
      for await (const chunk of request) {
        size += chunk.length;
        if (size > limit) throw new WorkerError("input_too_large", 413);
        chunks.push(chunk);
      }
      let payload;
      try {
        payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        throw new WorkerError("invalid_input", 400);
      }
      if (!payload || Array.isArray(payload) || Object.keys(payload).length !== 1 ||
          typeof payload.input !== "string") {
        throw new WorkerError("invalid_input", 400);
      }
      if (!accepting) throw new WorkerError("shutting_down", 503);
      if (acceptedAt.length >= 32) throw new WorkerError("queue_full", 503);
      acceptedAt.push(performance.now());
      let gone = false;
      response.once("close", () => { gone = !response.writableEnded; });
      const job = queue.then(async () => {
        if (fenced) throw new WorkerError("execution_owner_lost", 503);
        // nobody waits for a queued review whose client left; a started one always finishes
        if (gone) {
          completed.cancelled += 1;
          throw new WorkerError("client_gone", 499);
        }
        try {
          const result = await execute(payload.input, environment);
          completed.success += 1;
          if (result.unmirrored) {
            unmirrored += 1;
            console.error(JSON.stringify({ error: "session_not_mirrored", reason: result.unmirrored }));
          }
          return result.raw;
        } catch (error) {
          completed.failure += 1;
          throw error;
        }
      });
      queue = job.then(() => { acceptedAt.shift(); }, (error) => {
        acceptedAt.shift();
        if (error instanceof OwnerLostError) {
          fenced = true;
          process.exitCode = 1;
          shutdown();
        }
      });
      const raw = await job;
      reply(response, 200, raw, 0);
    } catch (error) {
      const failure = error instanceof WorkerError ? error : new WorkerError("review_failed");
      if (failure.status >= 500) {
        console.error(JSON.stringify({
          error: failure.kind, exitCode: failure.exitCode, reason: failure.reason ?? null,
        }));
      }
      reply(response, failure.status, JSON.stringify({
        error: failure.kind, exitCode: failure.exitCode,
      }), failure.exitCode);
    }
  }
  const server = cert
    ? httpsServer({ cert: await readFile(cert), key: await readFile(key) }, handle)
    : httpServer(handle);
  server.requestTimeout = 30_000;
  server.headersTimeout = 15_000;
  const shutdown = () => {
    accepting = false;
    server.close();
    server.closeIdleConnections();
  };
  // the drained queue lets the exec agent stop as well
  server.once("close", () => {
    fetch(`${environment.CODEX_AGENT_URL}/release`, {
      method: "POST", signal: AbortSignal.timeout(5000),
    }).catch(() => {});
  });
  process.once("SIGTERM", shutdown);
  process.once("SIGINT", shutdown);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  console.log(`CODEX_WORKER_READY ${cert ? "https" : "http"}://${isIP(address.address) === 6
    ? `[${address.address}]` : address.address}:${address.port}`);
}

main().catch(() => {
  console.error("Codex worker startup failed");
  process.exitCode = 1;
});
