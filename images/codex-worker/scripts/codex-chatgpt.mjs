import { createHash, randomBytes, randomUUID } from "node:crypto";
import { constants, fstatSync } from "node:fs";
import { chmod, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, sep } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const limit = 8 * 1024 * 1024;
const requestId = /^[a-f0-9]{32}$/;

class AuthError extends Error {}

function parseSession(value) {
  const auth = JSON.parse(value);
  if (auth?.auth_mode !== "chatgpt" || auth.OPENAI_API_KEY ||
      !["id_token", "access_token", "refresh_token"].every(
        (key) => typeof auth.tokens?.[key] === "string" && auth.tokens[key].length > 0,
      )) {
    throw new AuthError("Codex ChatGPT session is invalid; renew it with device login");
  }
  return JSON.stringify(auth, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]]))
      : item);
}

const digest = (value) => createHash("sha256").update(value).digest("hex");

async function syncDirectory(path) {
  const directory = await open(path, "r");
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}

async function writeAtomic(path, value) {
  const temporary = `${path}.${randomUUID()}`;
  try {
    const file = await open(temporary, "wx", 0o600);
    try {
      await file.writeFile(value);
      await file.sync();
    } finally {
      await file.close();
    }
    await rename(temporary, path);
    await syncDirectory(dirname(path));
  } finally {
    await rm(temporary, { force: true });
  }
}

// reads a file the model container could have replaced: no links, bounded size
async function readExchanged(path) {
  if (await realpath(dirname(path)) !== dirname(path)) {
    throw new AuthError("Codex ChatGPT exchange directory is invalid");
  }
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > limit) throw new AuthError("Codex ChatGPT exchange file is invalid");
    return await file.readFile("utf8");
  } finally {
    await file.close();
  }
}

async function main() {
  const {
    CI_API_V4_URL, CI_PROJECT_ID, CODEX_AUTH_STORE_TOKEN, CODEX_AUTH_STORE_PROJECT_ID,
    CODEX_AUTH_STATE_DIR, CODEX_AUTH_LOCK_FD, CODEX_EXCHANGE_DIR, CODEX_AGENT_URL,
    CODEX_GATEWAY_URL, CODEX_GATEWAY_KEY, CODEX_MODEL,
  } = process.env;
  const [target, ...extra] = process.argv.slice(2);
  const recovering = target === "--recover";
  if (!CI_API_V4_URL || !CI_PROJECT_ID || !CODEX_AUTH_STORE_TOKEN || !target || extra.length ||
      (!recovering && !isAbsolute(target))) {
    throw new AuthError("Codex ChatGPT runner requires its scoped auth store and an output path");
  }
  if (!/^[1-9]\d*$/.test(CODEX_AUTH_STORE_PROJECT_ID ?? "") ||
      CODEX_AUTH_STORE_PROJECT_ID === CI_PROJECT_ID) {
    throw new AuthError("Codex ChatGPT auth store requires a separate numeric project ID");
  }
  if (!CODEX_AUTH_STATE_DIR || !isAbsolute(CODEX_AUTH_STATE_DIR) || CODEX_AUTH_LOCK_FD !== "3") {
    throw new AuthError("Codex ChatGPT runner requires protected state and an exclusive worker owner");
  }
  if (!CODEX_EXCHANGE_DIR || !isAbsolute(CODEX_EXCHANGE_DIR) ||
      !/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(CODEX_AGENT_URL ?? "")) {
    throw new AuthError("Codex ChatGPT runner requires an exchange directory and a loopback exec agent");
  }
  let gateway;
  try {
    gateway = new URL(CODEX_GATEWAY_URL);
  } catch {
    gateway = null;
  }
  if (gateway?.protocol !== "https:" || gateway.username || gateway.password || gateway.search ||
      gateway.hash || !/^[A-Za-z0-9_-]{16,256}$/.test(CODEX_GATEWAY_KEY ?? "")) {
    throw new AuthError("Codex ChatGPT runner requires an HTTPS model gateway and its key");
  }
  // the gateway admits only models its key names, so the CLI default is not used
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(CODEX_MODEL ?? "")) {
    throw new AuthError("Codex ChatGPT runner requires the model name the gateway key allows");
  }
  const privateRoot = join(CODEX_AUTH_STATE_DIR, "private");
  await mkdir(privateRoot, { recursive: true, mode: 0o700 });
  await chmod(privateRoot, 0o700);
  const [exchange, stateRoot, owner] = await Promise.all([
    realpath(CODEX_EXCHANGE_DIR), realpath(privateRoot), stat(join(privateRoot, "owner.lock")),
  ]);
  const lease = fstatSync(3);
  const inside = (parent, child) => child === parent || child.startsWith(`${parent}${sep}`);
  if (!lease.isFile() || lease.dev !== owner.dev || lease.ino !== owner.ino ||
      inside(exchange, stateRoot) || inside(stateRoot, exchange)) {
    throw new AuthError("Codex ChatGPT state must have an exclusive owner outside the exchange");
  }
  const current = join(stateRoot, "current");
  const durableAuth = join(current, "auth.json");
  const baselinePath = join(current, "baseline.json");
  const poll = Number(process.env.CODEX_AGENT_POLL_MS ?? "1000");
  // one review deadline of the agent plus a margin
  const patience = Number(process.env.CODEX_AGENT_WAIT_MS ?? "1560000");

  // an agent that still runs a review owns its request directory
  async function idle() {
    const deadline = performance.now() + patience;
    for (;;) {
      try {
        const response = await fetch(`${CODEX_AGENT_URL}/status`, { signal: AbortSignal.timeout(5000) });
        if (response.ok && (await response.json()).busy === null) return;
      } catch {
        // an agent that does not answer is waited for like a busy one
      }
      if (performance.now() > deadline) throw new AuthError("Codex exec agent is unavailable or stayed busy");
      await sleep(poll);
    }
  }
  async function adopt(id) {
    const refreshed = parseSession(await readExchanged(join(exchange, id, "home", "auth.json")));
    if (refreshed !== parseSession(await readFile(durableAuth, "utf8"))) {
      await writeAtomic(durableAuth, refreshed);
    }
  }
  async function recover() {
    const leftovers = (await readdir(exchange)).filter((id) => requestId.test(id));
    const started = [];
    for (const id of leftovers) {
      try {
        await stat(join(exchange, id, "run.lock"));
        started.push(id);
      } catch (error) {
        if (!(error instanceof Error && error.code === "ENOENT")) throw error;
        await rm(join(exchange, id), { recursive: true, force: true });
      }
    }
    if (started.length > 1) {
      throw new AuthError("Codex ChatGPT exchange holds more than one interrupted review; reconcile it by hand");
    }
    for (const id of started) {
      try {
        await adopt(id);
      } catch {
        throw new AuthError("Codex ChatGPT interrupted review has no valid session; reconcile it by hand");
      }
      await rm(join(exchange, id), { recursive: true, force: true });
      console.log("Recovered the session of an interrupted Codex review");
    }
  }

  await idle();
  await recover();
  if (recovering) return;

  const url = new URL(
    `${CI_API_V4_URL}/projects/${CODEX_AUTH_STORE_PROJECT_ID}/variables/CODEX_REVIEW_AUTH_JSON_B64`,
  );
  url.searchParams.set("filter[environment_scope]", "codex-review");
  async function request(method, body) {
    const response = await fetch(url, {
      method,
      redirect: "error",
      signal: AbortSignal.timeout(15000),
      headers: { "PRIVATE-TOKEN": CODEX_AUTH_STORE_TOKEN, "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      throw new AuthError(`Codex ChatGPT auth store ${method} failed (${response.status})`);
    }
    const variable = await response.json();
    if (variable.environment_scope !== "codex-review" || !variable.protected ||
        !variable.masked || !variable.raw || typeof variable.value !== "string") {
      throw new AuthError("Codex ChatGPT auth store must be protected, masked and scoped to codex-review");
    }
    return parseSession(Buffer.from(variable.value, "base64").toString("utf8"));
  }

  const original = await request("GET");
  let missing = false;
  try {
    if (!(await stat(current)).isDirectory()) throw new AuthError("Codex ChatGPT working state is invalid");
  } catch (error) {
    if (!(error instanceof Error && error.code === "ENOENT")) throw error;
    missing = true;
  }
  if (missing) {
    const staged = await mkdtemp(join(stateRoot, ".bootstrap-"));
    try {
      await writeAtomic(join(staged, "auth.json"), original);
      await writeAtomic(join(staged, "baseline.json"), JSON.stringify({
        endpoint: url.href, digest: digest(original),
      }));
      await rename(staged, current);
      await syncDirectory(stateRoot);
    } finally {
      await rm(staged, { recursive: true, force: true });
    }
  }
  let baseline = JSON.parse(await readFile(baselinePath, "utf8"));
  if (baseline.endpoint !== url.href || !/^[a-f0-9]{64}$/.test(baseline.digest ?? "")) {
    throw new AuthError("Codex ChatGPT working state does not match its auth store");
  }
  async function saveBaseline(value) {
    const next = { endpoint: url.href, digest: digest(value) };
    await writeAtomic(baselinePath, JSON.stringify(next));
    baseline = next;
  }
  async function reconcile(remote) {
    const local = parseSession(await readFile(durableAuth, "utf8"));
    if (local === remote) {
      if (baseline.digest !== digest(local)) await saveBaseline(local);
      return;
    }
    if (digest(remote) !== baseline.digest) {
      throw new AuthError("Codex ChatGPT auth changed; drain the worker and explicitly reconcile or reseed");
    }
    const saved = await request("PUT", {
      value: Buffer.from(local).toString("base64"),
    });
    if (saved !== local) throw new AuthError("Codex ChatGPT auth store did not retain the refresh");
    await saveBaseline(local);
    console.log("Persisted refreshed Codex ChatGPT session");
  }
  await reconcile(original);
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);

  // the model container gets a copy of the session, never the private state
  const id = randomBytes(16).toString("hex");
  const directory = join(exchange, id);
  const home = join(directory, "home");
  await mkdir(home, { recursive: true, mode: 0o700 });
  await mkdir(join(directory, "review"), { mode: 0o700 });
  await writeFile(join(home, "config.toml"), [
    `model = ${JSON.stringify(CODEX_MODEL)}`,
    'model_provider = "gateway"',
    'forced_login_method = "chatgpt"',
    'cli_auth_credentials_store = "file"',
    'web_search = "disabled"',
    "[features]",
    "shell_tool = false",
    "code_mode_host = false",
    "sleep_tool = false",
    "view_image = false",
    "apps = false",
    "browser_use = false",
    "computer_use = false",
    "image_generation = false",
    "multi_agent = false",
    "multi_agent_v2 = false",
    "plugins = false",
    "skill_search = false",
    "[agents]",
    "enabled = false",
    "[tools.experimental_request_user_input]",
    "enabled = false",
    "[tools.update_plan]",
    "enabled = false",
    // the gateway checks its key and forwards the ChatGPT token to the Codex backend
    "[model_providers.gateway]",
    'name = "Gateway"',
    `base_url = ${JSON.stringify(gateway.href.replace(/\/$/, ""))}`,
    'wire_api = "responses"',
    "requires_openai_auth = true",
    `http_headers = { "x-bf-vk" = ${JSON.stringify(CODEX_GATEWAY_KEY)} }`,
    "",
  ].join("\n"), { mode: 0o600 });
  await writeAtomic(join(home, "auth.json"), await readFile(durableAuth, "utf8"));
  let exitCode = 1;
  try {
    try {
      const response = await fetch(`${CODEX_AGENT_URL}/runs/${id}`, {
        method: "POST", body: Buffer.concat(chunks),
      });
      const result = await response.json();
      if (response.ok && Number.isInteger(result.exitCode) && !result.signal) exitCode = result.exitCode;
    } catch {
      // a lost agent connection does not prove that the review stopped
      await idle();
    }
    if (exitCode === 0) {
      await writeFile(target, await readExchanged(join(directory, "review", "review.json")), {
        mode: 0o600, flag: "wx",
      });
    }
  } finally {
    try {
      await adopt(id);
    } catch {
      // the review ended here and the private state is intact: set the copy aside
      await rm(join(home, "config.toml"), { force: true });
      await rename(directory, join(exchange, `invalid-${id}`));
      throw new AuthError("Codex ChatGPT review left no valid session; its request directory is set aside");
    }
    await rm(directory, { recursive: true, force: true });
    if (digest(await readFile(durableAuth, "utf8")) !== baseline.digest) {
      try {
        await reconcile(await request("GET"));
      } catch (error) {
        if (exitCode !== 0) throw error;
        // the session is safe in the private state: keep the review, report the store
        console.error(error instanceof AuthError ? error.message : "Codex ChatGPT auth store is unreachable");
        exitCode = 3;
      }
    }
  }
  process.exitCode = exitCode;
}

main().catch((error) => {
  console.error(error instanceof AuthError ? error.message : "Codex ChatGPT session could not be loaded, run or persisted");
  process.exitCode = 1;
});
