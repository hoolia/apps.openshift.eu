import { spawn, spawnSync } from "node:child_process";
import { lstat, readdir, realpath } from "node:fs/promises";
import { createServer } from "node:http";
import { isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";

const limit = 8 * 1024 * 1024;
const schema = fileURLToPath(new URL("./codex-review.schema.json", import.meta.url));
const runLock = fileURLToPath(new URL("./codex-run-lock.py", import.meta.url));
const requestId = /^[a-f0-9]{32}$/;
const credential = /TOKEN|SECRET|PASSWORD|PRIVATE|KUBECONFIG|AUTH_JSON|(^|_)KEY(_|$)/;

class AgentError extends Error {
  constructor(kind, status) {
    super(kind);
    this.kind = kind;
    this.status = status;
  }
}

async function privateDirectory(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || (info.mode & 0o077) !== 0 || info.uid !== process.getuid()) {
    throw new AgentError("invalid_request", 400);
  }
}

async function main() {
  const exchangeSetting = process.env.CODEX_EXCHANGE_DIR;
  const port = Number(process.env.CODEX_AGENT_PORT ?? "3444");
  const binary = process.env.CODEX_BINARY || "/usr/local/bin/codex";
  const quiet = Number(process.env.CODEX_AGENT_QUIET_MS ?? "120000");
  const deadline = Number(process.env.CODEX_AGENT_RUN_TIMEOUT_MS ?? "1500000");
  if (Object.keys(process.env).some((name) => credential.test(name))) {
    throw new Error("Codex exec agent refuses an environment that names a credential");
  }
  if (!exchangeSetting || !isAbsolute(exchangeSetting) || !Number.isInteger(port) ||
      port < 0 || port > 65535 || !Number.isFinite(quiet) || quiet < 0 ||
      !Number.isFinite(deadline) || deadline <= 0) {
    throw new Error("Codex exec agent configuration is invalid");
  }
  const exchange = await realpath(exchangeSetting);
  const held = (id) => spawnSync("python3", [runLock, "--probe", join(exchange, id, "run.lock")],
    { stdio: "ignore" }).status !== 0;
  // runs that outlived an earlier agent still own their request directory
  const orphans = new Set((await readdir(exchange)).filter((id) => requestId.test(id) && held(id)));
  let active = null;
  let terminating = false;
  let released = false;
  let contact = performance.now();
  const busy = () => {
    for (const id of orphans) if (!held(id)) orphans.delete(id);
    return active ?? [...orphans][0] ?? null;
  };

  async function run(id, request) {
    if (!requestId.test(id)) throw new AgentError("invalid_request", 400);
    if (terminating && released) throw new AgentError("shutting_down", 503);
    if (busy()) throw new AgentError("busy", 409);
    active = id;
    try {
      const directory = join(exchange, id);
      const home = join(directory, "home");
      const work = join(directory, "review");
      for (const path of [directory, home, work]) await privateDirectory(path);
      if (await realpath(work) !== work || await realpath(home) !== home) {
        throw new AgentError("invalid_request", 400);
      }
      let size = 0;
      const chunks = [];
      for await (const chunk of request) {
        size += chunk.length;
        if (size > limit) throw new AgentError("input_too_large", 413);
        chunks.push(chunk);
      }
      const environment = Object.fromEntries(
        ["PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS"]
          .filter((name) => process.env[name] !== undefined).map((name) => [name, process.env[name]]),
      );
      const child = spawn("python3", [
        runLock, join(directory, "run.lock"), binary, "exec",
        "--ephemeral", "--sandbox", "read-only", "--ignore-rules",
        "--skip-git-repo-check", "--cd", work,
        "--output-schema", schema, "--output-last-message", join(work, "review.json"), "-",
      ], {
        cwd: work,
        env: { ...environment, CODEX_HOME: home, HOME: home, TMPDIR: directory },
        stdio: ["pipe", "ignore", "ignore"], detached: true,
      });
      // a review past its deadline ends with its whole process group
      const overdue = setTimeout(() => {
        try { process.kill(-child.pid, "SIGKILL"); } catch { /* already gone */ }
      }, deadline);
      const completion = new Promise((resolve) => {
        child.once("error", () => resolve({ exitCode: 1, signal: null }));
        child.once("close", (exitCode, signal) => resolve({ exitCode: exitCode ?? 1, signal }));
      });
      child.stdin.on("error", () => child.stdin.destroy());
      child.stdin.end(Buffer.concat(chunks));
      try {
        return await completion;
      } finally {
        clearTimeout(overdue);
      }
    } finally {
      active = null;
    }
  }

  const server = createServer(async (request, response) => {
    contact = performance.now();
    const reply = (status, body) => {
      if (response.destroyed) return;
      response.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
      response.end(JSON.stringify(body));
    };
    try {
      if (request.method === "GET" && request.url === "/status") {
        reply(200, { busy: busy() });
      } else if (request.method === "POST" && request.url === "/release") {
        released = true;
        reply(200, {});
      } else if (request.method === "POST" && request.url.startsWith("/runs/")) {
        reply(200, await run(request.url.slice("/runs/".length), request));
      } else {
        throw new AgentError("not_found", 404);
      }
    } catch (error) {
      request.resume();
      const failure = error instanceof AgentError ? error : new AgentError("run_failed", 500);
      reply(failure.status, { error: failure.kind });
    } finally {
      contact = performance.now();
    }
  });
  server.requestTimeout = 0;
  server.headersTimeout = 15_000;
  // the worker drains its queue after SIGTERM, so stay until it releases or goes quiet
  const stop = () => {
    terminating = true;
    const timer = setInterval(() => {
      if (busy() === null && (released || performance.now() - contact > quiet)) {
        clearInterval(timer);
        server.close();
        server.closeAllConnections();
      }
    }, 100);
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  console.log(`CODEX_AGENT_READY http://127.0.0.1:${server.address().port}`);
}

main().catch((error) => {
  console.error(error instanceof Error && /^Codex exec agent/.test(error.message)
    ? error.message : "Codex exec agent startup failed");
  process.exitCode = 1;
});
