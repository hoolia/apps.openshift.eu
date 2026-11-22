import assert from "node:assert/strict";
import { run, startAgent } from "./codex-runtime-fixture.mjs";
import { mkdir, mkdtemp, readFile, readdir, readlink, realpath, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const runner = fileURLToPath(new URL("./codex-chatgpt.mjs", import.meta.url));
const lock = fileURLToPath(new URL("./codex-worker-lock.py", import.meta.url));
const schema = fileURLToPath(new URL("./codex-review.schema.json", import.meta.url));
const flatten = (tools, prefix = "") => (tools ?? []).flatMap((tool) =>
  tool.tools ? flatten(tool.tools, `${prefix}${tool.name}.`) : [`${prefix}${tool.name ?? tool.type}`]);


for (const enabled of [true, false]) {
  test(enabled ? "executes the control expression with the native host enabled" :
    "rejects tool execution with the review configuration", { timeout: 120000 }, async (t) => {
    const binary = process.env.CODEX_RUNTIME_BINARY;
    assert.ok(binary, "CODEX_RUNTIME_BINARY must point to the verified CLI");
    const directory = await realpath(await mkdtemp(join(tmpdir(), "codex-runtime-test-")));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const home = join(directory, "home");
    const exchange = join(directory, "exchange");
    await mkdir(home, { mode: 0o700 });
    await mkdir(exchange, { mode: 0o700 });
    const environment = { PATH: process.env.PATH, HOME: directory, TMPDIR: directory };
    const variable = {
      value: Buffer.from(JSON.stringify({
        auth_mode: "chatgpt", OPENAI_API_KEY: null,
        tokens: { id_token: "synthetic-id", access_token: "synthetic-access", refresh_token: "synthetic-refresh" },
      })).toString("base64"),
      environment_scope: "codex-review", protected: true, masked: true, raw: true,
    };
    const store = createServer((_request, response) => {
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify(variable));
    });
    await new Promise((resolve) => store.listen(0, "127.0.0.1", resolve));
    t.after(() => new Promise((resolve) => store.close(resolve)));
    // takes the review configuration the runner hands to the exec agent
    const collector = join(directory, "collect.cjs");
    await writeFile(collector, `#!${process.execPath}
      const { copyFileSync } = require("node:fs");
      copyFileSync(require("node:path").join(process.env.CODEX_HOME, "config.toml"),
        ${JSON.stringify(join(directory, "config.toml"))});
      process.exit(3);
    `, { mode: 0o700 });
    const agentUrl = await startAgent(t, {
      PATH: process.env.PATH, CODEX_EXCHANGE_DIR: exchange, CODEX_BINARY: collector,
    });
    const collected = await run("python3", [lock, process.execPath, runner, join(directory, "review.json")], {
      env: {
        ...environment, CI_PROJECT_ID: "27",
        CODEX_AUTH_STATE_DIR: join(directory, "state"),
        CODEX_EXCHANGE_DIR: exchange, CODEX_AGENT_URL: agentUrl, CODEX_AGENT_POLL_MS: "25",
        CODEX_AUTH_STORE_PROJECT_ID: "2121",
        CI_API_V4_URL: `http://127.0.0.1:${store.address().port}/api/v4`,
        CODEX_AUTH_STORE_TOKEN: "synthetic-store-token",
        CODEX_GATEWAY_URL: "https://gateway.invalid/chatgpt_passthrough/backend-api/codex", CODEX_GATEWAY_KEY: "synthetic-gateway-0001",
      },
    });
    assert.equal(collected.code, 3, collected.stderr);
    const config = await readFile(join(directory, "config.toml"), "utf8");
    assert.doesNotMatch(config, /^model\s*=/m);
    await writeFile(join(home, "config.toml"), config, { mode: 0o600 });

    const requests = [];
    const callId = "execution-boundary";
    const server = createServer(async (request, response) => {
      let body = "";
      for await (const chunk of request) body += chunk;
      if (request.method !== "POST" || request.url !== "/responses") {
        response.writeHead(404).end();
        return;
      }
      requests.push(JSON.parse(body));
      if (requests.length !== 1) {
        response.writeHead(400, { "content-type": "application/json" })
          .end(JSON.stringify({ error: { message: "Fixture complete", type: "invalid_request_error" } }));
        return;
      }
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.end([
        { type: "response.created", response: { id: "fixture" } },
        {
          type: "response.output_item.done",
          item: {
            type: "custom_tool_call", call_id: callId, namespace: "functions", name: "exec",
            input: 'text("PUBLIC" + "_" + (2121 * 3) + "_CANARY");',
          },
        },
        {
          type: "response.completed",
          response: {
            id: "fixture",
            usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
          },
        },
      ].map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(""));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(() => {
      server.closeAllConnections();
      return new Promise((resolve) => server.close(resolve));
    });
    const result = await run(binary, [
      "exec", "--ephemeral", "--sandbox", "read-only", "--ignore-rules", "--skip-git-repo-check",
      "--cd", directory, "--output-schema", schema,
      "-c", 'model_provider="fixture"',
      "-c", 'model_providers.fixture.name="Public loopback fixture"',
      "-c", `model_providers.fixture.base_url="http://127.0.0.1:${server.address().port}"`,
      "-c", 'model_providers.fixture.wire_api="responses"',
      "-c", "model_providers.fixture.requires_openai_auth=false",
      "-c", "model_providers.fixture.request_max_retries=0",
      "-c", "model_providers.fixture.stream_max_retries=0",
      "-c", "analytics.enabled=false",
      ...(enabled ? ["-c", "features.code_mode_host=true"] : []),
      "-",
    ], { env: { ...environment, HOME: home, CODEX_HOME: home } });
    assert.equal(result.signal, null, result.stderr);
    assert.equal(result.code, 1, result.stderr);
    assert.equal(requests.length, 2, result.stderr);
    const tools = [
      ...flatten(requests[0].tools),
      ...(requests[0].input ?? []).filter((item) => item.type === "additional_tools")
        .flatMap((item) => flatten(item.tools)),
    ].sort();
    assert.deepEqual(tools, ["functions.exec", "functions.wait"]);
    const outputs = (requests[1].input ?? [])
      .filter((item) => item.type === "custom_tool_call_output" && item.call_id === callId);
    assert.equal(outputs.length, 1);
    const output = JSON.stringify(outputs[0].output);
    if (enabled) {
      assert.match(output, /PUBLIC_6363_CANARY/);
    } else {
      assert.doesNotMatch(output, /PUBLIC_6363_CANARY/);
      assert.match(output, /code-mode host is disabled/);
    }
  });
}

test("persists a native ephemeral refresh when inference fails", { timeout: 120000 }, async (t) => {
  const binary = process.env.CODEX_RUNTIME_BINARY;
  assert.ok(binary, "CODEX_RUNTIME_BINARY must point to the verified CLI");
  const directory = await realpath(await mkdtemp(join(tmpdir(), "codex-refresh-test-")));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const exchange = join(directory, "exchange");
  const state = join(directory, "state");
  await mkdir(exchange, { mode: 0o700 });
  const payload = Buffer.from(JSON.stringify({
    "https://api.openai.com/auth": {
      chatgpt_account_id: "synthetic-account", chatgpt_plan_type: "plus",
    },
  })).toString("base64url");
  const idToken = `eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.${payload}.c2lnbmF0dXJl`;
  const auth = {
    auth_mode: "chatgpt", OPENAI_API_KEY: null,
    tokens: {
      id_token: idToken, access_token: "synthetic-access-old",
      refresh_token: "synthetic-refresh-old", account_id: "synthetic-account",
    },
    last_refresh: "2000-01-01T00:00:00Z",
  };
  const variable = {
    value: Buffer.from(JSON.stringify(auth)).toString("base64"),
    environment_scope: "codex-review", protected: true, masked: true, raw: true,
  };
  let exchanges = 0;
  let inferenceAttempts = 0;
  async function checkNativeLease() {
    const pid = (await readFile(join(directory, "native.pid"), "utf8")).trim();
    const [request] = await readdir(exchange);
    assert.match(request, /^[a-f0-9]{32}$/);
    assert.equal(await readlink(`/proc/${pid}/fd/3`), join(exchange, request, "run.lock"));
    assert.doesNotMatch(await readFile(`/proc/${pid}/environ`, "utf8"), /_TOKEN=|synthetic-store/);
  }
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    response.setHeader("content-type", "application/json");
    const path = new URL(request.url, "http://localhost").pathname;
    if (path === "/api/v4/projects/2121/variables/CODEX_REVIEW_AUTH_JSON_B64") {
      assert.equal(request.headers["private-token"], "synthetic-store-token");
      if (request.method === "PUT") Object.assign(variable, JSON.parse(body));
      response.end(JSON.stringify(variable));
    } else if (path === "/oauth/token") {
      assert.equal(JSON.parse(body).refresh_token, "synthetic-refresh-old");
      exchanges++;
      response.end(JSON.stringify({
        id_token: idToken, access_token: "synthetic-access-new",
        refresh_token: "synthetic-refresh-new",
      }));
    } else if (path.endsWith("/wham/config/bundle")) {
      response.end("{}");
    } else {
      if (path.endsWith("/responses")) {
        inferenceAttempts++;
        assert.equal(request.headers.authorization, "Bearer synthetic-access-new");
        await checkNativeLease();
      }
      response.writeHead(400).end(JSON.stringify({
        error: { message: "Synthetic inference failure", type: "invalid_request_error" },
      }));
    }
  });
  server.on("upgrade", async (request, socket) => {
    inferenceAttempts++;
    assert.equal(request.headers.authorization, "Bearer synthetic-access-new");
    await checkNativeLease();
    socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  // the agent fixes the CLI arguments; this stand-in only points it at the fixture
  const native = join(directory, "native.cjs");
  await writeFile(native, `#!${process.execPath}
    const base = ${JSON.stringify(base)};
    const args = process.argv.slice(2);
    process.env.CODEX_REFRESH_TOKEN_URL_OVERRIDE = base + "/oauth/token";
    const child = require("node:child_process").spawn(${JSON.stringify(binary)}, [
      ...args.slice(0, -1), "-c", 'chatgpt_base_url="' + base + '/backend-api/"',
      "-c", 'model_provider="fixture"',
      "-c", 'model_providers.fixture.name="Public loopback fixture"',
      "-c", 'model_providers.fixture.base_url="' + base + '"',
      "-c", 'model_providers.fixture.wire_api="responses"',
      "-c", "model_providers.fixture.requires_openai_auth=true",
      "-c", "model_providers.fixture.request_max_retries=0",
      "-c", "model_providers.fixture.stream_max_retries=0",
      "-c", "analytics.enabled=false", "-",
    ], { env: process.env, stdio: ["inherit", "inherit", "inherit", 3] });
    require("node:fs").writeFileSync(${JSON.stringify(join(directory, "native.pid"))}, String(child.pid), { mode: 0o600 });
    child.once("error", () => { process.exitCode = 1; });
    child.once("exit", (code) => { process.exitCode = code ?? 1; });
  `, { mode: 0o700 });
  const agentUrl = await startAgent(t, {
    PATH: process.env.PATH, CODEX_EXCHANGE_DIR: exchange, CODEX_BINARY: native,
  });
  const result = await run("python3", [lock, process.execPath, runner, join(directory, "review.json")], {
    env: {
      PATH: process.env.PATH, HOME: directory, TMPDIR: directory,
      CODEX_AUTH_STATE_DIR: state,
      CODEX_EXCHANGE_DIR: exchange, CODEX_AGENT_URL: agentUrl, CODEX_AGENT_POLL_MS: "25",
      CI_PROJECT_ID: "27", CI_API_V4_URL: `${base}/api/v4`,
      CODEX_AUTH_STORE_PROJECT_ID: "2121", CODEX_AUTH_STORE_TOKEN: "synthetic-store-token",
      CODEX_GATEWAY_URL: "https://gateway.invalid/chatgpt_passthrough/backend-api/codex", CODEX_GATEWAY_KEY: "synthetic-gateway-0001",
    },
  });
  assert.equal(result.signal, null, result.stderr);
  assert.equal(result.code, 1, result.stderr);
  assert.equal(exchanges, 1, result.stderr);
  assert.ok(inferenceAttempts > 0, result.stderr);
  const saved = JSON.parse(Buffer.from(variable.value, "base64"));
  assert.equal(saved.tokens.access_token, "synthetic-access-new");
  assert.equal(saved.tokens.refresh_token, "synthetic-refresh-new");
  assert.notEqual(saved.last_refresh, auth.last_refresh);
  assert.match(result.stdout, /Persisted refreshed Codex ChatGPT session/);
  const durablePath = join(state, "private", "current", "auth.json");
  assert.deepEqual(await readdir(exchange), []);
  const durable = JSON.parse(await readFile(durablePath, "utf8"));
  assert.deepEqual(durable.tokens, saved.tokens);
});
