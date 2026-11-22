import assert from "node:assert/strict";
import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { bounded, client, fixture, processRun, rawReview } from "./codex-worker-fixture.mjs";

async function completed(t) {
  const f = await fixture(t);
  const run = f.launch("first");
  await f.started("first");
  f.releases.get("first")();
  assert.equal((await bounded(run.done)).stdout, rawReview);
  return f;
}

test("stale remote publication cannot reseed newer native auth", { timeout: 15000 }, async (t) => {
  const f = await completed(t);
  const stale = JSON.parse(Buffer.from(f.variable.value, "base64"));
  stale.tokens.refresh_token = "synthetic-old";
  f.variable.value = Buffer.from(JSON.stringify(stale)).toString("base64");
  const result = await bounded(f.launch("stale").done);
  assert.equal(result.code, 1);
  assert.equal(result.stdout, "");
  assert.equal(f.starts.length, 1);
  const local = JSON.parse(await readFile(join(f.directory, "state/private/current/auth.json"), "utf8"));
  assert.equal(local.tokens.refresh_token, "synthetic-rotated-first");
});

test("accepted publication with a lost response recovers before another native run",
  { timeout: 15000 }, async (t) => {
    const f = await fixture(t, { dropSaveResponse: true });
    const first = f.launch("lost-response");
    await f.started("lost-response");
    f.releases.get("lost-response")();
    assert.equal((await bounded(first.done)).stdout, rawReview);
    const second = f.launch("recovered");
    assert.equal((await f.started("recovered")).previous, "synthetic-rotated-lost-response");
    f.releases.get("recovered")();
    assert.equal((await bounded(second.done)).stdout, rawReview);
    assert.equal(f.writes.length, 2);
    assert.equal(f.writes[1].tokens.refresh_token, "synthetic-rotated-recovered");
  });

for (const fault of ["missing auth", "invalid baseline", "wrong endpoint"]) {
  test(`existing state with ${fault} cannot bootstrap from a remote snapshot`,
    { timeout: 15000 }, async (t) => {
      const f = await completed(t);
      const current = join(f.directory, "state/private/current");
      if (fault === "missing auth") await rm(join(current, "auth.json"));
      else if (fault === "invalid baseline") await writeFile(join(current, "baseline.json"), "{");
      else {
        const baseline = JSON.parse(await readFile(join(current, "baseline.json"), "utf8"));
        baseline.endpoint = "https://invalid.example/api/v4/projects/1/variables/OTHER";
        await writeFile(join(current, "baseline.json"), JSON.stringify(baseline));
      }
      const remote = f.variable.value;
      const result = await bounded(f.launch("corrupt").done);
      assert.equal(result.code, 1);
      assert.equal(result.stdout, "");
      assert.equal(f.starts.length, 1);
      assert.equal(f.variable.value, remote);
    });
}

test("drained explicit reseed preserves the quarantined native state", { timeout: 15000 }, async (t) => {
  const f = await completed(t);
  f.service.child.kill("SIGTERM");
  assert.equal((await bounded(f.service.done)).code, 0);
  const privateRoot = join(f.directory, "state/private");
  await rename(join(privateRoot, "current"), join(privateRoot, "quarantined"));
  const fresh = JSON.parse(Buffer.from(f.variable.value, "base64"));
  fresh.tokens.refresh_token = "synthetic-owner-reseed";
  f.variable.value = Buffer.from(JSON.stringify(fresh)).toString("base64");
  const replacement = f.replacement();
  const ready = await replacement.line(/CODEX_WORKER_READY (http\S+)/);
  const run = processRun(client, {
    PATH: process.env.PATH, CODEX_WORKER_URL: ready[1], CODEX_WORKER_TOKEN: "synthetic-worker",
  });
  t.after(async () => { run.child.kill("SIGKILL"); await run.done; });
  run.child.stdin.end(JSON.stringify({
    id: "reseed", backend: f.environment.CI_API_V4_URL.replace(/\/api\/v4$/, ""),
  }));
  assert.equal((await f.started("reseed")).previous, "synthetic-owner-reseed");
  f.releases.get("reseed")();
  assert.equal((await bounded(run.done)).stdout, rawReview);
  const quarantined = JSON.parse(await readFile(join(privateRoot, "quarantined/auth.json"), "utf8"));
  assert.equal(quarantined.tokens.refresh_token, "synthetic-rotated-first");
  replacement.child.kill("SIGTERM");
  assert.equal((await bounded(replacement.done)).code, 0);
});
