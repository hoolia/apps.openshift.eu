import { test } from "bun:test";
import assert from "node:assert/strict";
import { chmod, cp, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function docker(...args) {
  const process = Bun.spawn(["docker", ...args], {
    env: globalThis.process.env,
    stdout: "pipe",
    stderr: "pipe",
    timeout: 10000,
  });
  const [stdout, stderr, status] = await Promise.all([
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
    process.exited,
  ]);
  assert.equal(status, 0, `docker ${args[0]}: ${stderr}`);
  return stdout.trim();
}

function inbox() {
  const messages = [];
  const listeners = new Set();
  return {
    push(message) {
      messages.push(message);
      for (const listener of listeners) listener();
    },
    take(predicate) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          listeners.delete(check);
          reject(new Error("Expected Janus message was not received"));
        }, 10000);
        function check() {
          const index = messages.findIndex(predicate);
          if (index < 0) return;
          clearTimeout(timer);
          listeners.delete(check);
          resolve(messages.splice(index, 1)[0]);
        }
        listeners.add(check);
        check();
      });
    },
  };
}

test("arbitrary UID serves VideoRoom API and delivers websocket session events", async () => {
  const image = process.env.JANUS_IMAGE;
  assert.ok(image, "JANUS_IMAGE must name the locally built image");
  const events = inbox();
  const receiver = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request, server) {
      if (new URL(request.url).pathname !== "/proxy"
        || request.headers.get("sec-websocket-protocol") !== "janus-events") {
        return new Response("Janus event endpoint required", { status: 400 });
      }
      if (server.upgrade(request, {
        headers: { "Sec-WebSocket-Protocol": "janus-events" },
      })) return;
      return new Response("WebSocket required", { status: 400 });
    },
    websocket: {
      message(socket, message) {
        const parsed = JSON.parse(String(message));
        for (const event of Array.isArray(parsed) ? parsed : [parsed]) {
          events.push(event);
        }
      },
    },
  });
  const config = await mkdtemp(join(tmpdir(), "janus-test-"));
  let container;
  let socket;
  let logs;
  try {
    await cp(new URL("./config/", import.meta.url), config, { recursive: true });
    await chmod(config, 0o755);
    await writeFile(join(config, "janus.eventhandler.wsevh.jcfg"), `
general: {
    enabled = true
    events = "sessions"
    grouping = false
    backend = "ws://127.0.0.1:${receiver.port}/proxy"
    subprotocol = "janus-events"
    events_cap_on_reconnect = 10
    events_max_queue = 100
}
`);
    container = await docker("run", "--detach", "--network", "host",
      "--user", "12345:23456", "--read-only", "--cap-drop", "ALL",
      "--security-opt", "no-new-privileges", "--tmpfs", "/tmp",
      "--mount", `type=bind,src=${config},dst=/etc/janus,readonly`, image);
    logs = Bun.spawn(["docker", "logs", "--follow", container], {
      env: process.env, stdout: "pipe", stderr: "inherit", timeout: 30000,
    });
    let startup = "";
    for await (const chunk of logs.stdout) {
      startup += new TextDecoder().decode(chunk);
      if (startup.includes("JANUS WebSockets transport plugin initialized!")) break;
    }
    assert.ok(startup.includes("JANUS WebSockets transport plugin initialized!"), startup);
    logs.kill();
    await logs.exited;

    socket = new WebSocket("ws://127.0.0.1:8188", "janus-protocol");
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("WebSocket open timed out")), 5000);
      socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener("error", () => {
        clearTimeout(timer);
        reject(new Error("Janus websocket connection failed"));
      }, { once: true });
    });
    const replies = inbox();
    socket.addEventListener("message", ({ data }) => replies.push(JSON.parse(String(data))));
    async function request(janus, fields = {}) {
      const transaction = crypto.randomUUID();
      const reply = replies.take((message) => message.transaction === transaction);
      socket.send(JSON.stringify({ janus, transaction, ...fields }));
      return await reply;
    }
    const info = await request("info");
    assert.equal(info.janus, "server_info");
    assert.equal(info.version_string, "1.4.3");
    assert.deepEqual(Object.keys(info.plugins), ["janus.plugin.videoroom"]);
    assert.deepEqual(Object.keys(info.transports), ["janus.transport.websockets"]);
    assert.equal(info.event_handlers, true);
    assert.deepEqual(Object.keys(info.events), ["janus.eventhandler.wsevh"]);
    assert.equal(info.data_channels, true);

    const created = await request("create");
    assert.equal(created.janus, "success");
    const session = created.data.id;
    const sessionEvent = await events.take((event) => event.session_id === session
      && event.type === 1 && event.event?.name === "created");
    assert.equal(sessionEvent.session_id, session);
    const attached = await request("attach", {
      session_id: session, plugin: "janus.plugin.videoroom",
    });
    assert.equal(attached.janus, "success");
    const listed = await request("message", {
      session_id: session, handle_id: attached.data.id, body: { request: "list" },
    });
    assert.equal(listed.plugindata.plugin, "janus.plugin.videoroom");
    assert.equal(listed.plugindata.data.videoroom, "success");
    assert.deepEqual(listed.plugindata.data.list, []);
    assert.equal((await request("destroy", { session_id: session })).janus, "success");
    await events.take((event) => event.session_id === session
      && event.type === 1 && event.event?.name === "destroyed");
    assert.equal((await request("destroy", { session_id: session })).janus, "error");

    const status = await docker("exec", container, "cat", "/proc/1/status");
    assert.match(status, /^Uid:\s+12345\s+12345\s+12345\s+12345$/m);
    const fds = await docker("exec", container, "sh", "-c",
      'for fd in /proc/1/fd/*; do readlink "$fd" || true; done');
    const sockets = new Set([...fds.matchAll(/socket:\[(\d+)\]/g)].map((match) => match[1]));
    const tcp = await docker("exec", container, "cat", "/proc/net/tcp", "/proc/net/tcp6");
    const listeners = tcp.split("\n").map((line) => line.trim().split(/\s+/))
      .filter((fields) => fields[3] === "0A" && sockets.has(fields[9]))
      .map((fields) => fields[1]);
    assert.deepEqual(listeners, ["0100007F:1FFC"]);
  } finally {
    socket?.close();
    logs?.kill();
    if (container) {
      try {
        console.log(await docker("logs", container));
      } finally {
        await docker("stop", "--time", "5", container);
        await docker("rm", container);
      }
    }
    receiver.stop(true);
    await rm(config, { recursive: true, force: true });
  }
}, 60000);
