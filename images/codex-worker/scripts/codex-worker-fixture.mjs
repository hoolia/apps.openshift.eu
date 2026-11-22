import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { EventEmitter, once } from "node:events";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const worker = fileURLToPath(new URL("./codex-worker.mjs", import.meta.url));
const lockHelper = fileURLToPath(new URL("./codex-worker-lock.py", import.meta.url));
const client = fileURLToPath(new URL("./codex-worker-client.mjs", import.meta.url));
const agent = fileURLToPath(new URL("./codex-exec-agent.mjs", import.meta.url));
const schema = fileURLToPath(new URL("./codex-review.schema.json", import.meta.url));
const session = {
  auth_mode: "chatgpt", OPENAI_API_KEY: null,
  tokens: { id_token: "synthetic-id", access_token: "synthetic-access", refresh_token: "synthetic-old" },
};
const rawReview = '{ "verdict": "BLOCKING", "review": "A synthetic finding." }\n';
const bounded = (promise) => {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Handshake timed out")), 8000);
  })]).finally(() => clearTimeout(timer));
};

function processRun(path, environment, detached = false) {
  const [command, ...args] = Array.isArray(path) ? path : path === worker
    ? ["python3", lockHelper, process.execPath, path] : [process.execPath, path];
  const child = spawn(command, args, {
    env: environment, detached, stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  const events = new EventEmitter();
  child.stdout.on("data", (chunk) => { stdout += chunk; events.emit("stdout", stdout); });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const done = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
  const line = (pattern) => bounded(new Promise((resolve) => {
    const inspect = (text) => {
      const match = text.match(pattern);
      if (match) { events.off("stdout", inspect); resolve(match); }
    };
    events.on("stdout", inspect);
    inspect(stdout);
  }));
  return { child, done, line };
}

async function fixture(t, options = {}) {
  const directory = await mkdtemp(join(tmpdir(), "codex-worker-test-"));
  const events = new EventEmitter();
  const starts = [];
  const releases = new Map();
  const writes = [];
  const children = [];
  const orphans = new Set();
  const killOwned = (pid) => {
    try { process.kill(pid, "SIGKILL"); }
    catch (error) { if (error.code !== "ESRCH") throw error; }
    orphans.delete(pid);
  };
  const variable = {
    value: Buffer.from(JSON.stringify(session)).toString("base64"),
    environment_scope: "codex-review", protected: true, masked: true, raw: true,
  };
  let active = 0;
  let maximum = 0;
  const backend = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    if (request.url === "/fixture-error") {
      events.emit("fixture-error", new Error(body));
      response.end("{}");
      return;
    }
    if (request.url === "/native") {
      const start = JSON.parse(body);
      starts.push(start);
      active += 1;
      maximum = Math.max(maximum, active);
      releases.set(start.id, () => {
        releases.delete(start.id); active -= 1; response.end("{}");
      });
      events.emit("start", start);
      return;
    }
    assert.equal(request.headers["private-token"], "synthetic-store");
    const url = new URL(request.url, "http://localhost");
    assert.equal(url.pathname, "/api/v4/projects/2121/variables/CODEX_REVIEW_AUTH_JSON_B64");
    assert.equal(url.searchParams.get("filter[environment_scope]"), "codex-review");
    if (options.rejectAuth) { response.writeHead(403).end("synthetic-private-backend"); return; }
    if (request.method === "PUT") {
      Object.assign(variable, JSON.parse(body));
      writes.push(JSON.parse(Buffer.from(variable.value, "base64").toString()));
      events.emit("write");
      if (options.dropSaveResponse && writes.length === 1) {
        request.socket.destroy();
        return;
      }
    }
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(variable));
  });
  await new Promise((resolve) => backend.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${backend.address().port}`;
  const native = join(directory, "native.mjs");
  await writeFile(native, `#!${process.execPath}
import assert from "node:assert/strict";
import { fstatSync, readFileSync, realpathSync, renameSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
let phase = "input";
try {
let input = "";
for await (const chunk of process.stdin) input += chunk;
const job = JSON.parse(input);
const args = process.argv.slice(2);
const work = args[args.indexOf("--cd") + 1];
const output = args[args.indexOf("--output-last-message") + 1];
phase = "arguments";
assert.deepEqual(args, ["exec", "--ephemeral", "--sandbox", "read-only",
  "--ignore-rules", "--skip-git-repo-check", "--cd", work,
  "--output-schema", ${JSON.stringify(schema)}, "--output-last-message", output, "-"]);
assert.equal(process.cwd(), realpathSync(work));
assert.equal(statSync(work).mode & 0o777, 0o700);
assert.equal(statSync(process.env.CODEX_HOME).mode & 0o777, 0o700);
phase = "environment";
assert.equal(fstatSync(3).isFile(), true);
for (const key of Object.keys(process.env)) {
  assert.doesNotMatch(key, /TOKEN|KEY|CI_|HUB|NODE_OPTIONS|CODEX_AUTH|CODEX_WORKER|CODEX_EXCHANGE/);
}
assert.doesNotMatch(JSON.stringify(process.env), /synthetic-(?:store|worker|hub|job)/);
if (process.platform === "linux") {
  // the parent is the exec agent; no worker process is an ancestor
  const ancestor = readFileSync("/proc/" + process.ppid + "/environ", "utf8");
  assert.doesNotMatch(ancestor, /TOKEN|synthetic-(?:store|worker|hub|job)|GATEWAY_KEY/);
}
assert.equal(statSync(join(process.env.CODEX_HOME, "auth.json")).isFile(), true);
assert.equal(statSync(join(process.env.CODEX_HOME, "auth.json")).nlink, 1);
assert.doesNotMatch(readFileSync(join(process.env.CODEX_HOME, "config.toml"), "utf8"), /^model\\s*=/m);
phase = "rotation";
const authPath = realpathSync(join(process.env.CODEX_HOME, "auth.json"));
const auth = JSON.parse(readFileSync(authPath, "utf8"));
const previous = auth.tokens.refresh_token;
auth.tokens.refresh_token = "synthetic-rotated-" + job.id;
const commit = () => {
  writeFileSync(authPath + ".new", JSON.stringify(auth), { mode: 0o600 });
  renameSync(authPath + ".new", authPath);
};
if (!job.beforeRefresh) commit();
console.log("synthetic-private-stdout");
console.error("synthetic-private-stderr");
await fetch(job.backend + "/native", { method: "POST", body: JSON.stringify({
  id: job.id, previous, home: process.env.CODEX_HOME, authPath,
  pid: process.pid,
}) });
if (job.beforeRefresh) commit();
writeFileSync(output, job.malformed ? '{"verdict":"OK"}' : ${JSON.stringify(rawReview)});
process.exit(job.exitCode ?? 0);
} catch (error) {
  await fetch(${JSON.stringify(base)} + "/fixture-error", {
    method: "POST", body: phase + ":" + error.name + ":" + error.code,
  });
  process.exit(1);
}
`, { mode: 0o700 });
  const environment = {
    PATH: process.env.PATH,
    CODEX_WORKER_HOST: "127.0.0.1", CODEX_WORKER_PORT: "0",
    CODEX_WORKER_TOKEN: "synthetic-worker",
    CODEX_AUTH_STORE_TOKEN: "synthetic-store", CODEX_AUTH_STORE_PROJECT_ID: "2121",
    CODEX_AUTH_STATE_DIR: join(directory, "state"),
    CODEX_EXCHANGE_DIR: join(directory, "exchange"), CODEX_AGENT_POLL_MS: "25",
    CI_API_V4_URL: `${base}/api/v4`, CI_PROJECT_ID: "27",
    CODEX_GATEWAY_URL: "https://gateway.invalid/chatgpt_passthrough/backend-api/codex",
    CODEX_GATEWAY_KEY: "synthetic-gateway-0001",
    HUB_READ_TOKEN: "synthetic-hub", CI_JOB_TOKEN: "synthetic-job",
    NODE_OPTIONS: "", ...options.environment,
  };
  let service;
  await mkdir(environment.CODEX_AUTH_STATE_DIR, { mode: 0o700 });
  await mkdir(environment.CODEX_EXCHANGE_DIR, { mode: 0o700 });
  const agents = [];
  async function startAgent(port = "0") {
    const run = processRun(agent, {
      PATH: process.env.PATH, CODEX_EXCHANGE_DIR: environment.CODEX_EXCHANGE_DIR,
      CODEX_AGENT_PORT: port, CODEX_BINARY: native, CODEX_AGENT_QUIET_MS: "60000",
    });
    agents.push(run);
    const ready = await run.line(/CODEX_AGENT_READY (http\S+)/);
    return { ...run, url: ready[1] };
  }
  const executor = await startAgent();
  environment.CODEX_AGENT_URL = executor.url;
  t.after(async () => {
    try {
      for (const pid of orphans) killOwned(pid);
      for (const release of releases.values()) release();
      for (const child of children) child.child.kill("SIGKILL");
      await Promise.all(children.map((child) => child.done));
      if (service) {
        service.child.kill("SIGTERM");
        try {
          const result = await bounded(service.done);
          assert.doesNotMatch(result.stdout + result.stderr, /synthetic-/);
        } finally { service.child.kill("SIGKILL"); }
      }
      for (const run of agents) run.child.kill("SIGKILL");
      await Promise.all(agents.map((run) => run.done));
    } finally {
      backend.closeAllConnections();
      await new Promise((resolve) => backend.close(resolve));
      for (const start of starts) await rm(dirname(start.home), { recursive: true, force: true });
      await rm(directory, { recursive: true, force: true });
    }
  });
  service = processRun(worker, environment, true);
  const ready = await service.line(/CODEX_WORKER_READY (http\S+)/);
  const url = ready[1];
  function launch(id, extra = {}) {
    const run = processRun(client, {
      PATH: process.env.PATH, CODEX_WORKER_URL: url, CODEX_WORKER_TOKEN: "synthetic-worker",
      ...options.clientEnvironment,
    }, true);
    children.push(run);
    run.child.stdin.end(JSON.stringify({ backend: base, id, ...extra }));
    return run;
  }
  const started = (id) => bounded(starts.find((start) => !id || start.id === id)
    ? Promise.resolve(starts.find((start) => !id || start.id === id))
    : new Promise((resolve, reject) => {
      events.once("fixture-error", reject);
      const listener = (start) => {
        if (!id || start.id === id) { events.off("start", listener); resolve(start); }
      };
      events.on("start", listener);
    }));
  function replacement() {
    const run = processRun(worker, environment);
    children.push(run);
    return run;
  }
  return { url, launch, started, releases, writes, starts, variable, service, directory, replacement, environment,
    orphan: (start) => { orphans.add(start.pid); }, killOwned, executor, startAgent,
    runners: (pid) => spawnSync("ps", ["-Ao", "pid=,ppid="], { encoding: "utf8" }).stdout
      .trim().split("\n").map((line) => line.trim().split(/\s+/).map(Number))
      .filter(([, parent]) => parent === pid).map(([child]) => child),
    maximum: () => maximum,
    write: () => bounded(writes.length ? Promise.resolve() : once(events, "write")) };
}

export { agent, bounded, client, fixture, lockHelper, processRun, rawReview, worker };
