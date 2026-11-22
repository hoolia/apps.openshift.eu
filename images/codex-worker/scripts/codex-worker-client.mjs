import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { isIP } from "node:net";

const limit = 8 * 1024 * 1024;
class ClientError extends Error {
  constructor(kind, exitCode = 1) {
    super(kind);
    this.exitCode = exitCode;
  }
}

async function main() {
  const url = new URL(process.env.CODEX_WORKER_URL);
  const token = process.env.CODEX_WORKER_TOKEN;
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const local = host === "localhost" || host === "::1" ||
    (isIP(host) === 4 && host.startsWith("127."));
  if (!token || url.username || url.password || url.href.includes("#") || url.search ||
      (url.pathname !== "/" && url.pathname !== "/reviews") ||
      (url.protocol !== "https:" && !(url.protocol === "http:" && local))) {
    throw new ClientError("invalid_client_config");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > limit) throw new ClientError("input_too_large");
    chunks.push(chunk);
  }
  const body = JSON.stringify({ input: Buffer.concat(chunks).toString("utf8") });
  if (Buffer.byteLength(body) > limit) throw new ClientError("input_too_large");
  url.pathname = "/reviews";
  const result = await new Promise((resolve, reject) => {
    const request = (url.protocol === "https:" ? httpsRequest : httpRequest)(url, {
      method: "POST",
      signal: AbortSignal.timeout(30 * 60 * 1000),
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "content-length": Buffer.byteLength(body),
      },
    }, (response) => {
      const data = [];
      let length = 0;
      response.on("data", (chunk) => {
        length += chunk.length;
        if (length > limit) response.destroy(new ClientError("response_too_large"));
        else data.push(chunk);
      });
      response.once("error", reject);
      response.once("end", () => resolve({
        status: response.statusCode,
        exitCode: response.headers["x-codex-exit-code"],
        raw: Buffer.concat(data).toString("utf8"),
      }));
    });
    request.once("error", reject);
    request.end(body);
  });
  if (result.status !== 200 || result.exitCode !== "0") {
    const exitCode = Number(result.exitCode);
    throw new ClientError("worker_request_failed",
      Number.isInteger(exitCode) && exitCode > 0 && exitCode <= 255 ? exitCode : 1);
  }
  const review = JSON.parse(result.raw);
  if (!review || Array.isArray(review) || Object.keys(review).length !== 2 ||
      !["OK", "BLOCKING"].includes(review.verdict) ||
      typeof review.review !== "string" || !review.review.trim()) {
    throw new ClientError("invalid_review");
  }
  process.stdout.write(result.raw);
}

main().catch((error) => {
  console.error(error instanceof ClientError ? error.message : "Codex worker request failed");
  process.exitCode = error instanceof ClientError ? error.exitCode : 1;
});
