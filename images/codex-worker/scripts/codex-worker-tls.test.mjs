import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { request } from "node:https";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { bounded, client, fixture, lockHelper, processRun, rawReview, worker } from "./codex-worker-fixture.mjs";

test("worker client verifies TLS and completes a trusted request", { timeout: 300000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "codex-worker-certificate-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const cert = join(directory, "certificate.pem");
  const key = join(directory, "key.pem");
  const openssl = spawn("openssl", [
    "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=codex-worker-fixture", "-addext", "subjectAltName=IP:127.0.0.1",
    "-keyout", key, "-out", cert,
  ], { stdio: "ignore" });
  t.after(() => openssl.kill("SIGKILL"));
  const generated = await bounded(new Promise((resolve, reject) => {
    openssl.once("error", reject);
    openssl.once("close", resolve);
  }));
  assert.equal(generated, 0);
  const f = await fixture(t, {
    environment: { CODEX_WORKER_TLS_CERT_FILE: cert, CODEX_WORKER_TLS_KEY_FILE: key },
    clientEnvironment: { NODE_EXTRA_CA_CERTS: cert },
  });
  const untrusted = processRun(client, {
    PATH: process.env.PATH, CODEX_WORKER_URL: f.url, CODEX_WORKER_TOKEN: "synthetic-worker",
  });
  t.after(async () => { untrusted.child.kill("SIGKILL"); await untrusted.done; });
  untrusted.child.stdin.end("prepared");
  const rejected = await bounded(untrusted.done);
  assert.equal(rejected.code, 1);
  assert.equal(rejected.stdout, "");
  assert.deepEqual(f.starts, []);
  const trusted = f.launch("trusted");
  await f.started("trusted");
  f.releases.get("trusted")();
  assert.equal((await bounded(trusted.done)).stdout, rawReview);
  assert.equal(f.writes[0].tokens.refresh_token, "synthetic-rotated-trusted");
});

async function metricsFixture(t, rejectPut = false) {
  const directory = await mkdtemp(join(tmpdir(), "codex-worker-metrics-"));
  const cert = join(directory, "certificate.pem");
  const key = join(directory, "key.pem");
  const clock = join(directory, "clock");
  const preload = join(directory, "clock.mjs");
  await writeFile(clock, "1000");
  await writeFile(preload, [
    'import { readFileSync } from "node:fs";',
    `Object.defineProperty(performance, "now", { value: () => Number(readFileSync(${JSON.stringify(clock)}, "utf8")) });`,
  ].join("\n"));
  const openssl = spawn("openssl", [
    "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=codex-worker-metrics", "-addext", "subjectAltName=IP:127.0.0.1",
    "-keyout", key, "-out", cert,
  ], { stdio: "ignore" });
  t.after(() => openssl.kill("SIGKILL"));
  assert.equal(await bounded(new Promise((resolve, reject) => {
    openssl.once("error", reject);
    openssl.once("close", resolve);
  })), 0);
  const variable = {
    value: Buffer.from(JSON.stringify({
      auth_mode: "chatgpt", OPENAI_API_KEY: null,
      tokens: { id_token: "synthetic-id", access_token: "synthetic-access", refresh_token: "synthetic-old" },
    })).toString("base64"),
    environment_scope: "codex-review", protected: true, masked: true, raw: true,
  };
  const nativeRequests = [];
  let nativeReady = Promise.withResolvers();
  const store = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    if (req.url === "/native") {
      nativeRequests.push({ start: JSON.parse(body), release: () => res.end("{}") });
      nativeReady.resolve();
      return;
    }
    assert.equal(req.headers["private-token"], "synthetic-store");
    if (req.method === "PUT") {
      assert.ok(rejectPut);
      res.writeHead(503).end("synthetic-private-store-failure");
      return;
    }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(variable));
  });
  await new Promise((resolve) => store.listen(0, "127.0.0.1", resolve));
  const backend = `http://127.0.0.1:${store.address().port}`;
  const clientEnvironment = { NODE_EXTRA_CA_CERTS: cert };
  const f = await fixture(t, {
    environment: {
      CODEX_WORKER_TLS_CERT_FILE: cert, CODEX_WORKER_TLS_KEY_FILE: key,
      ...(rejectPut ? { CI_API_V4_URL: `${backend}/api/v4` } : {}),
    },
    clientEnvironment,
  });
  f.service.child.kill("SIGTERM");
  assert.equal((await bounded(f.service.done)).code, 0);
  const service = processRun([
    "python3", lockHelper, process.execPath, "--import", preload, worker,
  ], f.environment);
  const ready = await service.line(/CODEX_WORKER_READY (https\S+)/);
  const url = ready[1];
  clientEnvironment.CODEX_WORKER_URL = url;
  t.after(async () => {
    service.child.kill("SIGTERM");
    try {
      const result = await bounded(service.done);
      assert.equal(result.code, 0);
      assert.doesNotMatch(result.stdout + result.stderr, /synthetic-/);
    } finally { service.child.kill("SIGKILL"); }
    store.closeAllConnections();
    await new Promise((resolve) => store.close(resolve));
    await rm(directory, { recursive: true, force: true });
  });
  const ca = await readFile(cert);
  const call = (path, options = {}) => bounded(new Promise((resolve, reject) => {
    const req = request(new URL(path, url), { ca, ...options }, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.once("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
      res.once("error", reject);
    });
    req.once("error", reject);
    req.end(options.body);
  }));
  let rare = null;
  const metrics = async () => {
    const response = await call("/metrics");
    assert.equal(response.status, 200);
    assert.match(response.headers["content-type"], /^text\/plain; version=0\.0\.4/);
    assert.equal(response.headers["cache-control"], "no-store");
    const lines = response.body.trimEnd().split("\n");
    assert.deepEqual(lines.filter((line) => line.startsWith("#")), [
      "# TYPE codex_worker_reviews_total counter",
      "# TYPE codex_worker_session_mirror_failures_total counter",
      "# TYPE codex_worker_pending_reviews gauge",
      "# TYPE codex_worker_oldest_review_age_seconds gauge",
    ]);
    const samples = lines.filter((line) => !line.startsWith("#"));
    const names = [
      'codex_worker_reviews_total{result="success"}',
      'codex_worker_reviews_total{result="failure"}',
      'codex_worker_reviews_total{result="cancelled"}',
      "codex_worker_session_mirror_failures_total",
      "codex_worker_pending_reviews", "codex_worker_oldest_review_age_seconds",
    ];
    assert.equal(samples.length, names.length);
    const [success, failure, cancelled, unmirrored, pending, age] = samples.map((line, index) => {
      assert.equal(line.slice(0, line.lastIndexOf(" ")), names[index]);
      const value = line.slice(line.lastIndexOf(" ") + 1);
      assert.match(value, /^\d+(?:\.\d+)?$/);
      return Number(value);
    });
    rare = { cancelled, unmirrored };
    return [success, failure, pending, age];
  };
  const nextNative = async () => {
    if (!nativeRequests.length) await bounded(nativeReady.promise);
    nativeReady = Promise.withResolvers();
    return nativeRequests.shift();
  };
  return { ...f, backend, nextNative, call, metrics, rare: () => rare, tick: (value) => writeFile(clock, String(value)) };
}

test("metrics expose accepted queued and running TLS reviews through idle reset",
  { timeout: 60000 }, async (t) => {
    const f = await metricsFixture(t);
    assert.deepEqual(await f.metrics(), [0, 0, 0, 0]);
    for (const [body, token, status] of [
      ['{"input":"synthetic-private-prompt"}', "wrong", 401],
      ["{", "synthetic-worker", 400],
      ['{"input":1}', "synthetic-worker", 400],
    ]) {
      const response = await f.call("/reviews", {
        method: "POST", body, headers: { authorization: `Bearer ${token}` },
      });
      assert.equal(response.status, status);
    }
    assert.deepEqual(await f.metrics(), [0, 0, 0, 0]);
    const first = f.launch("first");
    await f.started("first");
    assert.deepEqual(await f.metrics(), [0, 0, 1, 0]);
    const queued = Array.from({ length: 32 }, (_, index) => ({
      id: `queued-${index}`, run: f.launch(`queued-${index}`, { backend: f.backend }),
    }));
    const rejected = await bounded(Promise.race(queued.map(async ({ id, run }) =>
      ({ id, result: await run.done }))));
    assert.equal(rejected.result.code, 1);
    assert.equal(rejected.result.stdout, "");
    assert.equal(rejected.result.stderr.trim(), "worker_request_failed");
    await f.tick(6000);
    assert.deepEqual(await f.metrics(), [0, 0, 32, 5]);
    assert.deepEqual(JSON.parse((await f.call("/healthz")).body), { live: true });
    const accepted = queued.filter(({ id }) => id !== rejected.id);
    f.releases.get("first")();
    assert.equal((await bounded(first.done)).stdout, rawReview);
    let next = await f.nextNative();
    assert.deepEqual(await f.metrics(), [1, 0, 31, 5]);
    assert.equal(next.start.previous, "synthetic-rotated-first");
    for (let index = 0; index < accepted.length; index += 1) {
      const { run } = accepted.find(({ id }) => id === next.start.id);
      next.release();
      const result = await bounded(run.done);
      assert.equal(result.code, 0);
      assert.equal(result.stdout, rawReview);
      if (index + 1 < accepted.length) next = await f.nextNative();
    }
    assert.deepEqual(await f.metrics(), [32, 0, 0, 0]);
    assert.equal(f.maximum(), 1);
  });

test("metrics count native failure as accepted execution failure", { timeout: 300000 }, async (t) => {
  const f = await metricsFixture(t);
  const run = f.launch("synthetic-private-prompt", { exitCode: 17 });
  await f.started("synthetic-private-prompt");
  await f.tick(3500);
  assert.deepEqual(await f.metrics(), [0, 0, 1, 2.5]);
  f.releases.get("synthetic-private-prompt")();
  const result = await bounded(run.done);
  assert.equal(result.code, 17);
  assert.equal(result.stdout, "");
  assert.doesNotMatch(result.stderr, /synthetic-/);
  assert.deepEqual(await f.metrics(), [0, 1, 0, 0]);
  assert.deepEqual(f.rare(), { cancelled: 0, unmirrored: 0 });
});

test("a refused store write keeps the review and counts a mirror failure", { timeout: 300000 }, async (t) => {
  const f = await metricsFixture(t, true);
  const run = f.launch("synthetic-private-prompt");
  await f.started("synthetic-private-prompt");
  f.releases.get("synthetic-private-prompt")();
  const result = await bounded(run.done);
  assert.equal(result.code, 0);
  assert.equal(result.stdout, rawReview);
  assert.deepEqual(await f.metrics(), [1, 0, 0, 0]);
  assert.deepEqual(f.rare(), { cancelled: 0, unmirrored: 1 });
});
