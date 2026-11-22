import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { bounded, client, fixture, lockHelper, processRun, rawReview, worker } from "./codex-worker-fixture.mjs";

for (const signal of ["SIGTERM", "SIGKILL"]) {
  for (const beforeRefresh of [true, false]) {
    test(`CI ${signal} ${beforeRefresh ? "before" : "after"} native refresh preserves writeback`,
      { timeout: 15000 }, async (t) => {
        const f = await fixture(t);
        const run = f.launch("cancelled", { beforeRefresh });
        const start = await f.started("cancelled");
        process.kill(-run.child.pid, signal);
        assert.equal((await bounded(run.done)).signal, signal);
        f.releases.get("cancelled")();
        await f.write();
        assert.equal(f.writes[0].tokens.refresh_token, "synthetic-rotated-cancelled");
        const next = f.launch("next");
        await f.started("next");
        f.releases.get("next")();
        assert.equal((await bounded(next.done)).stdout, rawReview);
        await assert.rejects(readFile(join(start.home, "auth.json")), { code: "ENOENT" });
      });
  }
}

test("worker and agent drain accepted execution together on shutdown", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  const first = f.launch("first");
  const second = f.launch("second");
  const firstStart = await f.started();
  f.service.child.kill("SIGTERM");
  f.executor.child.kill("SIGTERM");
  f.releases.get(firstStart.id)();
  f.releases.delete(firstStart.id);
  const start = await f.started(firstStart.id === "first" ? "second" : "first");
  assert.equal(start.previous, `synthetic-rotated-${firstStart.id}`);
  f.releases.get(start.id)();
  assert.equal((await bounded(first.done)).stdout, rawReview);
  assert.equal((await bounded(second.done)).stdout, rawReview);
  assert.equal((await bounded(f.service.done)).code, 0);
  assert.equal((await bounded(f.executor.done)).code, 0);
  assert.equal(f.maximum(), 1);
  assert.equal(f.writes.length, 2);
  assert.deepEqual(await readdir(f.environment.CODEX_EXCHANGE_DIR), []);
});

test("queued review whose client left is not started", { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  const sample = async (name) => Number((await (await fetch(`${f.url}/metrics`)).text())
    .split("\n").find((line) => line.startsWith(name)).split(" ").at(-1));
  const first = f.launch("first");
  await f.started("first");
  const second = f.launch("second");
  while (await sample("codex_worker_pending_reviews") < 2) await sleep(20);
  process.kill(-second.child.pid, "SIGKILL");
  await bounded(second.done);
  await sleep(200);
  f.releases.get("first")();
  assert.equal((await bounded(first.done)).stdout, rawReview);
  const third = f.launch("third");
  await f.started("third");
  f.releases.get("third")();
  assert.equal((await bounded(third.done)).stdout, rawReview);
  assert.deepEqual(f.starts.map((start) => start.id), ["first", "third"]);
  assert.equal(await sample('codex_worker_reviews_total{result="cancelled"}'), 1);
  assert.equal(await sample('codex_worker_reviews_total{result="failure"}'), 0);
});

const durable = async (f) => JSON.parse(await readFile(
  join(f.directory, "state/private/current/auth.json"), "utf8")).tokens.refresh_token;
const backend = (f) => f.environment.CI_API_V4_URL.replace(/\/api\/v4$/, "");
// resolves "waiting" while a replacement worker has not announced readiness
async function waiting(replacement) {
  const ready = replacement.line(/CODEX_WORKER_READY (http\S+)/);
  ready.catch(() => {});
  const state = await Promise.race([ready.then(() => "ready"), sleep(400, "waiting")]);
  return { ready, state };
}

test("restarted worker waits for the agent's review and adopts its refresh", { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  const run = f.launch("survivor");
  const start = await f.started("survivor");
  f.orphan(start);
  process.kill(-f.service.child.pid, "SIGKILL");
  assert.equal((await bounded(f.service.done)).signal, "SIGKILL");
  assert.equal((await bounded(run.done)).code, 1);
  const replacement = f.replacement();
  const { ready, state } = await waiting(replacement);
  assert.equal(state, "waiting");
  assert.equal(await durable(f), "synthetic-old");
  f.releases.get("survivor")();
  const url = (await ready)[1];
  assert.equal(f.starts.length, 1);
  assert.equal(await durable(f), "synthetic-rotated-survivor");
  assert.deepEqual(await readdir(f.environment.CODEX_EXCHANGE_DIR), []);
  assert.equal(f.writes.length, 0);
  const next = processRun(client, {
    PATH: process.env.PATH, CODEX_WORKER_URL: url, CODEX_WORKER_TOKEN: "synthetic-worker",
  });
  t.after(async () => { next.child.kill("SIGKILL"); await next.done; });
  next.child.stdin.end(JSON.stringify({ id: "next", backend: backend(f) }));
  assert.equal((await f.started("next")).previous, "synthetic-rotated-survivor");
  assert.equal(f.writes[0].tokens.refresh_token, "synthetic-rotated-survivor");
  f.releases.get("next")();
  assert.equal((await bounded(next.done)).stdout, rawReview);
  replacement.child.kill("SIGTERM");
  assert.equal((await bounded(replacement.done)).code, 0);
});

test("orphan auth runner holds the restart lease until it has saved the refresh", { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  const run = f.launch("orphan");
  const start = await f.started("orphan");
  f.orphan(start);
  f.service.child.kill("SIGKILL");
  assert.equal((await bounded(f.service.done)).signal, "SIGKILL");
  assert.equal((await bounded(run.done)).code, 1);
  assert.equal((await bounded(f.replacement().done)).code, 1);
  f.releases.get("orphan")();
  await f.write();
  assert.equal(f.writes[0].tokens.refresh_token, "synthetic-rotated-orphan");
  let replacement;
  for (let attempt = 0; attempt < 80 && !replacement; attempt += 1) {
    const candidate = f.replacement();
    const ready = candidate.line(/CODEX_WORKER_READY /);
    ready.catch(() => {});
    if (await Promise.race([ready.then(() => true), candidate.done.then(() => false)])) {
      replacement = candidate;
    } else {
      await sleep(50);
    }
  }
  assert.ok(replacement);
  assert.equal(f.starts.length, 1);
  replacement.child.kill("SIGTERM");
  assert.equal((await bounded(replacement.done)).code, 0);
});

test("auth runner death stops the worker before another native owner starts",
  { timeout: 20000 }, async (t) => {
    const f = await fixture(t);
    const first = f.launch("owner-lost");
    const start = await f.started("owner-lost");
    f.orphan(start);
    const [runner] = f.runners(f.service.child.pid);
    assert.ok(runner);
    f.killOwned(runner);
    const second = f.launch("must-not-start");
    assert.equal((await bounded(second.done)).code, 1);
    assert.equal((await bounded(first.done)).code, 1);
    assert.equal((await bounded(f.service.done)).code, 1);
    assert.equal(f.starts.length, 1);
    const retained = JSON.parse(await readFile(join(start.home, "auth.json"), "utf8"));
    assert.equal(retained.tokens.refresh_token, "synthetic-rotated-owner-lost");
    const replacement = f.replacement();
    const { ready, state } = await waiting(replacement);
    assert.equal(state, "waiting");
    f.releases.get("owner-lost")();
    await ready;
    assert.equal(f.starts.length, 1);
    assert.equal(await durable(f), "synthetic-rotated-owner-lost");
    replacement.child.kill("SIGTERM");
    assert.equal((await bounded(replacement.done)).code, 0);
  });

test("interrupted review without a valid session keeps the worker down", { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  const done = f.launch("seed");
  await f.started("seed");
  f.releases.get("seed")();
  assert.equal((await bounded(done.done)).stdout, rawReview);
  f.service.child.kill("SIGTERM");
  assert.equal((await bounded(f.service.done)).code, 0);
  const exchange = f.environment.CODEX_EXCHANGE_DIR;
  const never = join(exchange, "a".repeat(32));
  await mkdir(join(never, "home"), { recursive: true, mode: 0o700 });
  const first = f.replacement();
  await first.line(/CODEX_WORKER_READY /);
  first.child.kill("SIGTERM");
  assert.equal((await bounded(first.done)).code, 0);
  assert.deepEqual(await readdir(exchange), []);
  const broken = join(exchange, "b".repeat(32));
  await mkdir(join(broken, "home"), { recursive: true, mode: 0o700 });
  await writeFile(join(broken, "run.lock"), "");
  await writeFile(join(broken, "home", "auth.json"), '{"auth_mode":"apikey"}');
  const refused = await bounded(f.replacement().done);
  assert.equal(refused.code, 1);
  assert.doesNotMatch(refused.stdout, /CODEX_WORKER_READY/);
  assert.match(refused.stderr, /"reason":"Codex ChatGPT interrupted review has no valid session; reconcile it by hand"/);
  assert.deepEqual(await readdir(broken), ["home", "run.lock"]);
  assert.equal(await durable(f), "synthetic-rotated-seed");
});

test("service rejects inherited descriptor that is not its lease file", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  f.service.child.kill("SIGTERM");
  await bounded(f.service.done);
  const run = processRun(["python3", lockHelper, process.execPath, "--input-type=module", "-e", [
    'import { closeSync, openSync } from "node:fs";',
    'closeSync(3); openSync("/dev/null", "r");',
    `await import(${JSON.stringify(new URL("./codex-worker.mjs", import.meta.url).href)});`,
  ].join("\n")], f.environment);
  t.after(async () => { run.child.kill("SIGKILL"); await run.done; });
  const result = await bounded(run.done);
  assert.equal(result.code, 1);
  assert.doesNotMatch(result.stdout, /CODEX_WORKER_READY/);
});

for (const [name, extra, expected] of [
  ["native failure", { exitCode: 17 }, 17],
  ["malformed native output", { malformed: true }, 1],
]) {
  test(`${name} fails the client without leaking output`, { timeout: 15000 }, async (t) => {
    const f = await fixture(t);
    const run = f.launch("failure", extra);
    await f.started("failure");
    f.releases.get("failure")();
    const result = await bounded(run.done);
    assert.equal(result.code, expected);
    assert.equal(result.stdout, "");
    assert.doesNotMatch(result.stderr, /synthetic-private|synthetic-store|synthetic-worker/);
    assert.equal(f.writes.length, 1);
  });
}

test("auth backend rejection fails closed and liveness stays independent", { timeout: 15000 }, async (t) => {
  const f = await fixture(t, { rejectAuth: true });
  const run = f.launch("rejected");
  const result = await bounded(run.done);
  assert.equal(result.code, 1);
  assert.equal(result.stdout, "");
  assert.deepEqual(f.starts, []);
  assert.doesNotMatch(result.stderr, /synthetic-private/);
  assert.deepEqual(await (await fetch(`${f.url}/healthz`)).json(), { live: true });
  f.service.child.kill("SIGTERM");
  const log = await bounded(f.service.done);
  assert.match(log.stderr, /"error":"execution_failed","exitCode":1,"reason":"Codex ChatGPT auth store GET failed \(403\)"/);
});

test("worker rejects unauthorized, oversized and configurable review payloads", { timeout: 15000 }, async (t) => {
  const f = await fixture(t);
  for (const [body, token, status] of [
    ['{"input":"x"}', "wrong", 401], ["{", "synthetic-worker", 400],
    ['{"input":1}', "synthetic-worker", 400],
    ['{"input":"x","command":"false"}', "synthetic-worker", 400],
    [JSON.stringify({ input: "x".repeat(8 * 1024 * 1024) }), "synthetic-worker", 413],
  ]) {
    const response = await fetch(`${f.url}/reviews`, {
      method: "POST", headers: { authorization: `Bearer ${token}` }, body,
    });
    assert.equal(response.status, status);
    assert.doesNotMatch(await response.text(), /synthetic-worker|synthetic-store/);
  }
  assert.deepEqual(f.starts, []);
  assert.deepEqual(await readdir(join(f.directory, "state")), ["private"]);
  assert.deepEqual(await readdir(f.environment.CODEX_EXCHANGE_DIR), []);
});

test("client rejects unsafe URLs and worker requires TLS beyond loopback", async (t) => {
  const credentialUrl = new URL("http://127.0.0.1");
  credentialUrl.username = "synthetic-user";
  credentialUrl.password = credentialUrl.username;
  for (const url of ["http://example.com", credentialUrl.href,
    "http://127.0.0.1/#x", "http://127.0.0.1/#"]) {
    const run = processRun(client, { CODEX_WORKER_TOKEN: "synthetic-worker", CODEX_WORKER_URL: url });
    t.after(async () => { run.child.kill("SIGKILL"); await run.done; });
    run.child.stdin.end("prepared");
    const result = await bounded(run.done);
    assert.equal(result.code, 1);
    assert.equal(result.stdout, "");
  }
  const state = await mkdtemp(join(tmpdir(), "codex-worker-tls-test-"));
  t.after(() => rm(state, { recursive: true, force: true }));
  const run = processRun(worker, {
    PATH: process.env.PATH, CODEX_WORKER_TOKEN: "synthetic-worker", CODEX_AUTH_STATE_DIR: state,
  });
  t.after(async () => { run.child.kill("SIGKILL"); await run.done; });
  run.child.stdin.end();
  assert.equal((await bounded(run.done)).code, 1);
});

test("client rejects malformed HTTP success without printing partial review", async (t) => {
  const server = createServer((request, response) => {
    request.resume();
    response.writeHead(200, { "x-codex-exit-code": "0" }).end('{"verdict":"OK"}');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }));
  const run = processRun(client, {
    CODEX_WORKER_TOKEN: "synthetic-worker",
    CODEX_WORKER_URL: `http://127.0.0.1:${server.address().port}`,
  });
  t.after(async () => { run.child.kill("SIGKILL"); await run.done; });
  run.child.stdin.end("prepared");
  const result = await bounded(run.done);
  assert.equal(result.code, 1);
  assert.equal(result.stdout, "");
});

test("launcher restores private state modes widened by a volume remount", { timeout: 15000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "codex-worker-remount-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const state = join(directory, "state");
  await mkdir(join(state, "private"), { recursive: true });
  await chmod(state, 0o2770);
  await chmod(join(state, "private"), 0o2770);
  const run = processRun(["python3", lockHelper, process.execPath, "-e", ""],
    { PATH: process.env.PATH, CODEX_AUTH_STATE_DIR: state });
  run.child.stdin.end();
  assert.equal((await bounded(run.done)).code, 0);
  for (const path of [state, join(state, "private")]) {
    assert.equal((await stat(path)).mode & 0o777, 0o700);
  }
});
