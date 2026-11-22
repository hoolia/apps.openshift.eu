import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { agent, bounded, processRun } from "./codex-worker-fixture.mjs";

const id = (letter) => letter.repeat(32);

async function fixture(t, extra = {}) {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "codex-agent-test-")));
  const exchange = join(directory, "exchange");
  await mkdir(exchange, { mode: 0o700 });
  // records its arguments and environment, then waits for its release file
  const native = join(directory, "native.mjs");
  await writeFile(native, `#!${process.execPath}
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const args = process.argv.slice(2);
const output = args[args.indexOf("--output-last-message") + 1];
writeFileSync(join(process.env.CODEX_HOME, "seen.json"), JSON.stringify({
  args, input, environment: process.env, cwd: process.cwd(), pid: process.pid,
}));
while (!existsSync(join(process.env.CODEX_HOME, "release"))) await new Promise((r) => setTimeout(r, 20));
writeFileSync(output, "{}");
process.exit(Number(readFileSync(join(process.env.CODEX_HOME, "release"), "utf8") || 0));
`, { mode: 0o700 });
  const runs = [];
  t.after(async () => {
    for (const run of runs) run.child.kill("SIGKILL");
    await Promise.all(runs.map((run) => run.done));
    await rm(directory, { recursive: true, force: true });
  });
  async function start(environment = {}) {
    const run = processRun(agent, {
      PATH: process.env.PATH, CODEX_EXCHANGE_DIR: exchange, CODEX_AGENT_PORT: "0",
      CODEX_BINARY: native, CODEX_AGENT_QUIET_MS: "300", ...environment,
    });
    runs.push(run);
    return run;
  }
  async function request(name) {
    const home = join(exchange, name, "home");
    await mkdir(home, { recursive: true, mode: 0o700 });
    await mkdir(join(exchange, name, "review"), { mode: 0o700 });
    return { home, release: (code = "") => writeFile(join(home, "release"), code) };
  }
  if (extra.unstarted) return { directory, exchange, start, request };
  const run = await start();
  const url = (await run.line(/CODEX_AGENT_READY (http\S+)/))[1];
  const post = (path, body = "prepared") => fetch(`${url}${path}`, { method: "POST", body });
  const status = async () => (await (await fetch(`${url}/status`)).json()).busy;
  const seen = async (home) => {
    for (;;) {
      try { return JSON.parse(await readFile(join(home, "seen.json"), "utf8")); } catch { await sleep(20); }
    }
  };
  return { directory, exchange, start, request, run, url, post, status, seen, native };
}

for (const name of ["CODEX_AUTH_STORE_TOKEN", "CODEX_WORKER_TOKEN", "CODEX_WORKER_TLS_KEY_FILE",
  "CI_JOB_TOKEN", "HUB_READ_TOKEN", "GATEWAY_KEY", "OPENAI_API_KEY", "CODEX_REVIEW_AUTH_JSON_B64"]) {
  test(`agent refuses to start with ${name} in its environment`, async (t) => {
    const f = await fixture(t, { unstarted: true });
    const result = await bounded((await f.start({ [name]: "synthetic-credential" })).done);
    assert.equal(result.code, 1);
    assert.doesNotMatch(result.stdout, /CODEX_AGENT_READY/);
    assert.doesNotMatch(result.stdout + result.stderr, /synthetic-credential/);
  });
}

test("agent runs the fixed review command without any credential", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  assert.match(f.url, /^http:\/\/127\.0\.0\.1:\d+$/);
  const { home, release } = await f.request(id("a"));
  const pending = f.post(`/runs/${id("a")}`, "prepared text");
  const seen = await f.seen(home);
  const work = join(f.exchange, id("a"), "review");
  assert.deepEqual(seen.args.slice(0, 8), ["exec", "--ephemeral", "--sandbox", "read-only",
    "--ignore-rules", "--skip-git-repo-check", "--cd", work]);
  assert.equal(seen.args[8], "--output-schema");
  assert.match(seen.args[9], /codex-review\.schema\.json$/);
  assert.deepEqual(seen.args.slice(10), ["--output-last-message", join(work, "review.json"), "-"]);
  assert.equal(seen.input, "prepared text");
  // the Python launcher may add its locale coercion variable
  assert.deepEqual(Object.keys(seen.environment)
    .filter((key) => !["__CF_USER_TEXT_ENCODING", "LC_CTYPE"].includes(key)).sort(),
  ["CODEX_HOME", "HOME", "PATH", "TMPDIR"]);
  assert.equal(await f.status(), id("a"));
  await f.request(id("b"));
  assert.equal((await f.post(`/runs/${id("b")}`)).status, 409);
  await release("7");
  const response = await pending;
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { exitCode: 7, signal: null });
  assert.equal(await f.status(), null);
});

test("agent rejects requests that name anything but its own request directory", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  await f.request(id("a"));
  const outside = join(f.directory, "outside");
  await mkdir(join(outside, "home"), { recursive: true, mode: 0o700 });
  await mkdir(join(outside, "review"), { mode: 0o700 });
  await symlink(outside, join(f.exchange, id("c")));
  const linked = await f.request(id("d"));
  await rm(join(f.exchange, id("d"), "review"), { recursive: true });
  await symlink(join(outside, "review"), join(f.exchange, id("d"), "review"));
  const open = await f.request(id("e"));
  await chmod(open.home, 0o750);
  for (const [path, expected] of [
    [`/runs/${id("f")}`, 400], ["/runs/..%2F..%2Foutside", 400], ["/runs/outside", 400],
    [`/runs/${id("a")}?command=false`, 400], [`/runs/${id("a")}/review`, 400],
    [`/runs/${id("A")}`, 400], [`/runs/${id("c")}`, 400], [`/runs/${id("d")}`, 400],
    [`/runs/${id("e")}`, 400], ["/exec", 404], ["/runs", 404],
  ]) {
    assert.equal((await f.post(path)).status, expected, path);
  }
  assert.equal((await fetch(`${f.url}/runs/${id("a")}`)).status, 404);
  assert.equal((await f.post(`/runs/${id("a")}`, "x".repeat(8 * 1024 * 1024 + 1))).status, 413);
  assert.equal(await f.status(), null);
  await assert.rejects(readFile(join(linked.home, "seen.json")), { code: "ENOENT" });
});

test("replacement agent stays busy while a review of the dead agent runs", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  const { home, release } = await f.request(id("a"));
  const pending = f.post(`/runs/${id("a")}`).catch(() => null);
  const seen = await f.seen(home);
  t.after(() => { try { process.kill(seen.pid, "SIGKILL"); } catch { /* gone */ } });
  f.run.child.kill("SIGKILL");
  await bounded(f.run.done);
  await pending;
  const replacement = await f.start();
  const url = (await replacement.line(/CODEX_AGENT_READY (http\S+)/))[1];
  const status = async () => (await (await fetch(`${url}/status`)).json()).busy;
  assert.equal(await status(), id("a"));
  await f.request(id("b"));
  assert.equal((await fetch(`${url}/runs/${id("b")}`, { method: "POST", body: "x" })).status, 409);
  await release();
  while (await status() !== null) await sleep(20);
});

test("terminated agent finishes its review and waits for release or silence", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  const { release } = await f.request(id("a"));
  const pending = f.post(`/runs/${id("a")}`);
  while (await f.status() === null) await sleep(20);
  f.run.child.kill("SIGTERM");
  await sleep(500);
  assert.equal(await f.status(), id("a"));
  await release();
  assert.equal((await pending).status, 200);
  const second = await f.request(id("b"));
  const next = f.post(`/runs/${id("b")}`);
  await second.release();
  assert.equal((await next).status, 200);
  assert.equal((await f.post("/release")).status, 200);
  assert.equal((await bounded(f.run.done)).code, 0);
  const silent = await fixture(t);
  silent.run.child.kill("SIGTERM");
  assert.equal((await bounded(silent.run.done)).code, 0);
});

test("agent ends a review that runs past its deadline", { timeout: 15000 }, async (t) => {
  const f = await fixture(t, { unstarted: true });
  const run = await f.start({ CODEX_AGENT_RUN_TIMEOUT_MS: "400" });
  const url = (await run.line(/CODEX_AGENT_READY (http\S+)/))[1];
  await f.request(id("a"));
  const response = await fetch(`${url}/runs/${id("a")}`, { method: "POST", body: "prepared" });
  assert.deepEqual(await response.json(), { exitCode: 1, signal: "SIGKILL" });
  assert.equal((await (await fetch(`${url}/status`)).json()).busy, null);
  await f.request(id("b"));
  const next = fetch(`${url}/runs/${id("b")}`, { method: "POST", body: "prepared" });
  assert.equal((await next).status, 200);
});
