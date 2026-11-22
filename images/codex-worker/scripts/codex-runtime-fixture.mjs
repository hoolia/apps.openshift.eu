import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const agent = fileURLToPath(new URL("./codex-exec-agent.mjs", import.meta.url));

// starts the exec agent that owns the native CLI in the worker pod
export async function startAgent(t, environment) {
  const child = spawn(process.execPath, [agent], {
    env: { ...environment, CODEX_AGENT_PORT: "0" }, stdio: ["ignore", "pipe", "inherit"],
  });
  const closed = new Promise((resolve) => child.once("close", resolve));
  t.after(() => { child.kill("SIGKILL"); return closed; });
  let stdout = "";
  return new Promise((resolve, reject) => {
    child.stdout.on("data", (data) => {
      stdout += data;
      const ready = stdout.match(/CODEX_AGENT_READY (http\S+)/);
      if (ready) resolve(ready[1]);
    });
    closed.then(() => reject(new Error("Codex exec agent exited before it was ready")));
  });
}

export async function run(command, args, options) {
  const child = spawn(command, args, {
    ...options, stdio: ["pipe", "pipe", "pipe"], detached: true,
  });
  const killGroup = () => {
    if (!child.pid) return;
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch (error) {
      if (!(error instanceof Error && error.code === "ESRCH")) throw error;
    }
  };
  const timeout = setTimeout(killGroup, 45000);
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (data) => { stdout += data; });
  child.stderr.on("data", (data) => { stderr += data; });
  child.stdin.end("Public synthetic execution-boundary fixture.");
  try {
    const result = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    return { ...result, stdout, stderr };
  } finally {
    clearTimeout(timeout);
    killGroup();
  }
}
