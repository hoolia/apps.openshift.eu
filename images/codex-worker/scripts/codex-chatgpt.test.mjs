import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { agent, processRun } from "./codex-worker-fixture.mjs";

const runner = fileURLToPath(new URL("./codex-chatgpt.mjs", import.meta.url));
const lock = fileURLToPath(new URL("./codex-worker-lock.py", import.meta.url));
const session = (version) => ({
  auth_mode: "chatgpt",
  OPENAI_API_KEY: null,
  tokens: {
    id_token: `synthetic-id-${version}`,
    access_token: `synthetic-access-${version}`,
    refresh_token: `synthetic-refresh-${version}`,
  },
});
const encode = (auth) => Buffer.from(JSON.stringify(auth)).toString("base64");

async function fixture(t, options = {}) {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "codex-chatgpt-test-")));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const exchange = join(directory, "exchange");
  const log = join(directory, "model.log");
  await mkdir(exchange, { mode: 0o700 });
  await writeFile(log, "");
  await writeFile(join(directory, "outside.json"), '{"verdict":"OK","review":"Outside the exchange."}');
  const variable = {
    key: "CODEX_REVIEW_AUTH_JSON_B64",
    value: encode(session("current")),
    environment_scope: "codex-review",
    protected: true,
    masked: true,
    raw: true,
    ...options.variable,
  };
  const requests = [];
  const server = createServer(async (request, response) => {
    requests.push(request.method);
    const url = new URL(request.url, "http://localhost");
    if (url.pathname !== "/api/v4/projects/2121/variables/CODEX_REVIEW_AUTH_JSON_B64") {
      response.writeHead(403).end("synthetic-private-response");
      return;
    }
    assert.equal(url.searchParams.get("filter[environment_scope]"), "codex-review");
    assert.equal(request.headers["private-token"], "synthetic-store-token");
    if (options.rejectLoad && request.method === "GET") {
      response.writeHead(404).end("synthetic-private-response");
      return;
    }
    if (request.method === "PUT") {
      let body = "";
      for await (const chunk of request) body += chunk;
      if (options.rejectSave) {
        response.writeHead(403).end("synthetic-private-response");
        return;
      }
      Object.assign(variable, JSON.parse(body));
    }
    if (options.rotateStore && requests.length === 2) {
      variable.value = encode(session("operator-rotation"));
    }
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(variable));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  // stands in for the CLI: the exec agent starts it in the request home
  const model = join(directory, "model.mjs");
  await writeFile(model, `#!${process.execPath}
    import assert from "node:assert/strict";
    import { appendFileSync, existsSync, lstatSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
    import { join } from "node:path";
    const say = (line) => appendFileSync(${JSON.stringify(log)}, line + "\\n");
    const args = process.argv.slice(2);
    const output = args[args.indexOf("--output-last-message") + 1];
    const review = () => ${options.linkReview
      ? `symlinkSync(${JSON.stringify(join(directory, "outside.json"))}, output)`
      : `writeFileSync(output, '{"verdict":"OK","review":"Synthetic."}')`};
    const path = join(process.env.CODEX_HOME, "auth.json");
    const auth = JSON.parse(readFileSync(path, "utf8"));
    try {
      if (existsSync(${JSON.stringify(join(directory, "recovery"))})) {
        assert.equal(auth.tokens.refresh_token, "synthetic-refresh-refreshed", "recovered stale session");
        say("RECOVERY_COMPLETED");
        review();
        process.exit(0);
      }
      say("MODEL_STARTED " + process.pid);
      assert.equal(auth.tokens.refresh_token, "synthetic-refresh-current");
      assert.equal(lstatSync(path).isFile(), true);
      assert.equal(lstatSync(path).mode & 0o777, 0o600);
      assert.equal(path.startsWith(${JSON.stringify(exchange)}), true);
      const config = readFileSync(join(process.env.CODEX_HOME, "config.toml"), "utf8");
      assert.match(config, /model_provider = "gateway"/);
      assert.match(config, /base_url = "https:\\/\\/gateway\\.invalid\\/chatgpt_passthrough\\/backend-api\\/codex"/);
      assert.match(config, /requires_openai_auth = true/);
      assert.match(config, /http_headers = \\{ "x-bf-vk" = "synthetic-gateway-0001" \\}/);
      assert.match(config, /forced_login_method = "chatgpt"/);
      assert.match(config, /cli_auth_credentials_store = "file"/);
      assert.doesNotMatch(config, /^model\\s*=/m);
      for (const key of Object.keys(process.env)) {
        assert.doesNotMatch(key, /TOKEN|KEY|KUBECONFIG|CI_|BASH_ENV|NODE_OPTIONS|CODEX_AUTH|CODEX_EXCHANGE/);
      }
      assert.doesNotMatch(JSON.stringify(process.env), /synthetic-/);
    } catch (error) {
      say("MODEL_ASSERTION " + error.message.replaceAll("synthetic-", "leaked-"));
      process.exit(99);
    }
    ${options.refresh === false ? "" : `auth.tokens = ${JSON.stringify(session("refreshed").tokens)};`}
    ${options.breakAuth ? 'auth.auth_mode = "apikey";' : ""}
    writeFileSync(path, JSON.stringify(auth));
    ${options.terminationSignal ? `
      say("MODEL_REFRESHED");
      setInterval(() => {}, 60_000);
    ` : `
      review();
      say("MODEL_COMPLETED");
      process.exit(${options.modelExit ?? 0});
    `}
  `, { mode: 0o700 });
  const executor = processRun(agent, {
    PATH: process.env.PATH, CODEX_EXCHANGE_DIR: exchange, CODEX_AGENT_PORT: "0", CODEX_BINARY: model,
  });
  t.after(async () => { executor.child.kill("SIGKILL"); await executor.done; });
  const agentUrl = (await executor.line(/CODEX_AGENT_READY (http\S+)/))[1];
  const environment = {
      ...process.env,
      CODEX_AUTH_STATE_DIR: join(directory, "state"),
      CODEX_EXCHANGE_DIR: exchange,
      CODEX_AGENT_URL: agentUrl,
      CODEX_AGENT_POLL_MS: "25",
      CI_API_V4_URL: `http://127.0.0.1:${server.address().port}/api/v4`,
      CI_PROJECT_ID: "27",
      CODEX_AUTH_STORE_PROJECT_ID: options.storeProject ?? "2121",
      CODEX_AUTH_STORE_TOKEN: "synthetic-store-token",
      CODEX_REVIEW_AUTH_JSON_B64: encode(session("stale-job-snapshot")),
      CI_JOB_TOKEN: "synthetic-job-token",
      HUB_READ_TOKEN: "synthetic-read-token",
      REVIEW_GITLAB_TOKEN: "synthetic-write-token",
      GATEWAY_KEY: "synthetic-gateway-key",
      CODEX_GATEWAY_URL: options.gatewayUrl ?? "https://gateway.invalid/chatgpt_passthrough/backend-api/codex",
      CODEX_GATEWAY_KEY: options.gatewayKey ?? "synthetic-gateway-0001",
      OPENAI_API_KEY: "synthetic-billable-key",
  };
  let stdout = "";
  let stderr = "";
  let runs = 0;
  function run(detached) {
    runs += 1;
    const output = join(directory, `review-${runs}.json`);
    const child = spawn("python3", [lock, process.execPath, runner, output], {
      env: environment, stdio: ["ignore", "pipe", "pipe"], detached, timeout: 8000,
    });
    child.stdout.on("data", (data) => { stdout += data; });
    child.stderr.on("data", (data) => { stderr += data; });
    return { child, output, code: new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    }) };
  }
  const first = run(Boolean(options.terminationSignal));
  let code;
  if (options.terminationSignal) {
    while (!(await readFile(log, "utf8")).includes("MODEL_REFRESHED")) await sleep(20);
    process.kill(-first.child.pid, options.terminationSignal);
    await first.code;
    // the review outlives its worker; end it as the CLI would end, without a result
    process.kill(Number((await readFile(log, "utf8")).match(/MODEL_STARTED (\d+)/)[1]), "SIGKILL");
    await writeFile(join(directory, "recovery"), "");
    code = await run(false).code;
  } else {
    code = await first.code;
  }
  let review = null;
  try { review = await readFile(first.output, "utf8"); } catch { /* no review */ }
  stdout += await readFile(log, "utf8");
  assert.doesNotMatch(stdout + stderr, /synthetic-(?:id|access|refresh|store|read|write|gateway|billable|private)/);
  return { code, stdout, stderr, variable, requests, directory, review, leftovers: await readdir(exchange) };
}

test("loads current scoped auth and persists refresh without exposing store credentials", async (t) => {
  const result = await fixture(t);
  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /MODEL_COMPLETED/);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("refreshed"));
  assert.deepEqual(result.requests, ["GET", "GET", "PUT"]);
  assert.equal(result.review, '{"verdict":"OK","review":"Synthetic."}');
  assert.deepEqual(result.leftovers, []);
});

test("sets the request directory aside when the model leaves no valid session", async (t) => {
  const result = await fixture(t, { breakAuth: true });
  assert.equal(result.code, 1);
  assert.match(result.stdout, /MODEL_COMPLETED/);
  assert.equal(result.leftovers.length, 1);
  assert.match(result.leftovers[0], /^invalid-[a-f0-9]{32}$/);
  const aside = join(result.directory, "exchange", result.leftovers[0], "home");
  assert.deepEqual(await readdir(aside), ["auth.json"]);
  assert.deepEqual(result.requests, ["GET"]);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("current"));
  const durable = JSON.parse(await readFile(join(result.directory, "state/private/current/auth.json"), "utf8"));
  assert.deepEqual(durable, session("current"));
});

test("does not return a review that links outside the request directory", async (t) => {
  const result = await fixture(t, { linkReview: true });
  assert.equal(result.code, 1);
  assert.match(result.stdout, /MODEL_COMPLETED/);
  assert.equal(result.review, null);
  assert.deepEqual(result.leftovers, []);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("refreshed"));
});

for (const [name, storeProject] of [
  ["missing project", ""],
  ["hub project", "27"],
  ["project path", "group/project"],
  ["invalid project ID", "-1"],
]) {
  test(`rejects ${name} before reading credentials or starting the model`, async (t) => {
    const result = await fixture(t, { storeProject });
    assert.equal(result.code, 1);
    assert.deepEqual(result.requests, []);
    assert.doesNotMatch(result.stdout, /MODEL_STARTED/);
    assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("current"));
    assert.match(result.stderr, /Codex ChatGPT auth store requires a separate numeric project ID/);
  });
}

for (const [name, gateway] of [
  ["plain HTTP gateway", { gatewayUrl: "http://gateway.invalid/codex" }],
  ["gateway URL with credentials", { gatewayUrl: `https://${["user", "pass"].join(":")}@gateway.invalid/codex` }],
  ["missing gateway", { gatewayUrl: "" }],
  ["gateway key that would break the config", { gatewayKey: 'short" }\nmodel = "other' }],
]) {
  test(`rejects a ${name} before reading credentials or starting the model`, async (t) => {
    const result = await fixture(t, gateway);
    assert.equal(result.code, 1);
    assert.deepEqual(result.requests, []);
    assert.doesNotMatch(result.stdout, /MODEL_STARTED/);
    assert.match(result.stderr, /Codex ChatGPT runner requires an HTTPS model gateway and its key/);
  });
}

test("keeps an unchanged session without rewriting the store", async (t) => {
  const result = await fixture(t, { refresh: false });
  assert.equal(result.code, 0, result.stderr);
  assert.deepEqual(result.requests, ["GET"]);
});

test("persists a refresh even when the model fails and retains its exit code", async (t) => {
  const result = await fixture(t, { modelExit: 17 });
  assert.equal(result.code, 17, result.stderr);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("refreshed"));
});

for (const terminationSignal of ["SIGTERM", "SIGKILL"]) {
  test(`recovers refreshed auth after the worker process group receives ${terminationSignal}`, async (t) => {
    const result = await fixture(t, { terminationSignal });
    assert.match(result.stdout, /MODEL_REFRESHED/);
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stdout, /RECOVERY_COMPLETED/);
    assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("refreshed"));
  });
}

for (const [name, variable] of [
  ["non-ChatGPT authentication mode", { value: encode({ ...session("current"), auth_mode: "apikey" }) }],
  ["mixed API authentication", { value: encode({ ...session("current"), OPENAI_API_KEY: "synthetic-billable-key" }) }],
  ["missing refresh token", { value: encode({
    ...session("current"),
    tokens: { id_token: "synthetic-id-current", access_token: "synthetic-access-current" },
  }) }],
  ["unprotected store", { protected: false }],
  ["unmasked store", { masked: false }],
  ["wrong scope", { environment_scope: "*" }],
  ["expanded store", { raw: false }],
  ["invalid JSON", { value: Buffer.from("synthetic-private-invalid-json").toString("base64") }],
]) {
  test(`rejects ${name} before starting the model`, async (t) => {
    const result = await fixture(t, { variable });
    assert.equal(result.code, 1);
    assert.doesNotMatch(result.stdout, /MODEL_STARTED/);
    assert.deepEqual(result.requests, ["GET"]);
    assert.match(result.stderr, /Codex ChatGPT/);
  });
}

test("does not start the model when its scoped credential is unavailable", async (t) => {
  const result = await fixture(t, { rejectLoad: true });
  assert.equal(result.code, 1);
  assert.doesNotMatch(result.stdout, /MODEL_STARTED/);
  assert.match(result.stderr, /Codex ChatGPT auth store GET failed \(404\)/);
});

test("keeps the review and reports the store when refreshed auth cannot be saved", async (t) => {
  const result = await fixture(t, { rejectSave: true });
  assert.equal(result.code, 3);
  assert.equal(result.review, '{"verdict":"OK","review":"Synthetic."}');
  assert.match(result.stderr, /Codex ChatGPT auth store PUT failed/);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("current"));
});

test("does not overwrite an operator credential rotation during a review", async (t) => {
  const result = await fixture(t, { rotateStore: true });
  assert.equal(result.code, 3);
  assert.equal(result.review, '{"verdict":"OK","review":"Synthetic."}');
  assert.match(result.stderr, /Codex ChatGPT auth changed; drain the worker/);
  assert.deepEqual(result.requests, ["GET", "GET"]);
  assert.deepEqual(JSON.parse(Buffer.from(result.variable.value, "base64")), session("operator-rotation"));
});

test("recovery gives up on an agent that stays busy", async (t) => {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "codex-busy-test-")));
  const agentServer = createServer((request, response) => {
    response.setHeader("content-type", "application/json");
    response.end('{"busy":"' + "a".repeat(32) + '"}');
  });
  await new Promise((resolve) => agentServer.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    agentServer.closeAllConnections();
    await new Promise((resolve) => agentServer.close(resolve));
    await rm(directory, { recursive: true, force: true });
  });
  await mkdir(join(directory, "exchange"), { mode: 0o700 });
  const child = spawn("python3", [lock, process.execPath, runner, "--recover"], {
    env: {
      PATH: process.env.PATH,
      CODEX_AUTH_STATE_DIR: join(directory, "state"), CODEX_EXCHANGE_DIR: join(directory, "exchange"),
      CODEX_AGENT_URL: `http://127.0.0.1:${agentServer.address().port}`,
      CODEX_AGENT_POLL_MS: "25", CODEX_AGENT_WAIT_MS: "300",
      CI_API_V4_URL: "http://127.0.0.1:9/api/v4", CI_PROJECT_ID: "27",
      CODEX_AUTH_STORE_PROJECT_ID: "2121", CODEX_AUTH_STORE_TOKEN: "synthetic-store-token",
      CODEX_GATEWAY_URL: "https://gateway.invalid/codex", CODEX_GATEWAY_KEY: "synthetic-gateway-0001",
    },
    stdio: ["ignore", "ignore", "pipe"], timeout: 8000,
  });
  let stderr = "";
  child.stderr.on("data", (data) => { stderr += data; });
  const code = await new Promise((resolve) => child.once("close", resolve));
  assert.equal(code, 1);
  assert.match(stderr, /Codex exec agent is unavailable or stayed busy/);
});
