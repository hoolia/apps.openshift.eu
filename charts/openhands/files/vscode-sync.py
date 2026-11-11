#!/usr/bin/env python3
"""Keep the editor's chat (Continue) in step with the agent's models and MCP servers.

Every interval it writes CONFIG (~/.continue/config.yaml) from two sources the pod has
already discovered: the models the gateway lists through the token proxy, and the MCP
servers in the agent-server settings (the chart's own, the ones mcp-sync found in the
cluster and the ones the user added in the UI). Every model goes through the token proxy,
which adds the agent's credential, so the file holds no gateway key. The first model of
MODELS, else the first gateway model whose name (after the last "/") is in PREFERRED,
comes first and is the chat default. Only enabled MCP servers are written; a server that
logs in with OAuth is left out, because its login lives in the agent-server. The file is
JSON, which YAML reads, and is written only when its content changes. A file without the
MARKER line belongs to the user and is left alone.

The gateway is asked for its models once per MODELS_INTERVAL, not on every pass: one
listing makes the gateway ask every provider behind it. A pass in between uses the last
answer, and so does a pass whose read failed.

SETTINGS, a JSON map, is merged into the editor's machine settings once per change of a
key. Never exits on an error.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:18000"
PROXY = "http://127.0.0.1:{}".format(os.environ.get("TOKEN_PROXY_PORT", "8099"))
HOME = os.environ.get("HOME", "/home/openhands")
CONFIG = os.environ.get("CONFIG", os.path.join(HOME, ".continue", "config.yaml"))
# the agent-server starts code-server with this user data directory, one per editor port
MACHINE = os.environ.get("MACHINE_SETTINGS", os.path.join(
    HOME, ".local", "share", "openhands-code-server", os.environ.get("VSCODE_PORT", "8001"),
    "Machine", "settings.json"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
MODELS_INTERVAL = int(os.environ.get("MODELS_INTERVAL", "3600"))
MODELS = json.loads(os.environ.get("MODELS") or "[]")
PREFERRED = json.loads(os.environ.get("PREFERRED") or "[]")
GATEWAY_MODELS = os.environ.get("GATEWAY_MODELS", "true") == "true"
SETTINGS = json.loads(os.environ.get("SETTINGS") or "{}")
MARKER = "# Written by the openhands chart (vscode-sync); remove this line to keep your own edits"
# models the gateway serves for chat that cannot hold a conversation
SKIP = ("embed", "rerank", "whisper", "tts", "moderation")


def get(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None
    except (OSError, ValueError):
        return 0, None


# the gateway's last answer and when it came
_listed: list[dict] = []
_listed_at = 0.0


def gateway_models() -> list[dict]:
    """The gateway's models, read again MODELS_INTERVAL seconds after the last answer."""
    global _listed, _listed_at
    if _listed and time.monotonic() - _listed_at < MODELS_INTERVAL:
        return _listed
    status, listing = get(PROXY + "/v1/models", {"Authorization": "Bearer token-proxy"})
    if status == 200 and isinstance(listing, dict):
        found = [m for m in listing.get("data", []) if m.get("id")]
        if found:
            _listed, _listed_at = found, time.monotonic()
    # a read without models keeps the last answer and is tried again on the next pass
    return _listed


def agent_mcp() -> dict | None:
    status, settings = get(API + "/api/settings", {
        "X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", ""),
        "X-Expose-Secrets": "plaintext"})
    if status != 200 or not isinstance(settings, dict):
        return None
    return (settings.get("agent_settings") or {}).get("mcp_config") or {}


def model_entry(model: str, context: int | None, tools: bool) -> dict:
    entry = {"name": model.rsplit("/", 1)[-1], "provider": "openai", "model": model,
             "apiBase": PROXY + "/v1", "apiKey": "token-proxy",
             "roles": ["chat", "edit", "apply", "summarize"]}
    # agent mode needs tool_use; Continue does not detect it for gateway model names
    if tools:
        entry["capabilities"] = ["tool_use"]
    if context:
        entry["defaultCompletionOptions"] = {"contextLength": context}
    return entry


def models(listed: list[dict]) -> list[dict]:
    """Chosen models first, then every other gateway model, each name once."""
    listed = [m for m in listed if not any(s in m["id"].lower() for s in SKIP)]
    by_id = {m["id"]: m for m in listed}
    by_name = {}
    for m in listed:
        by_name.setdefault(m["id"].rsplit("/", 1)[-1], m["id"])
    first = list(MODELS)
    if not first:
        first = [by_name[n] for n in PREFERRED if n in by_name][:1]
    order = list(dict.fromkeys(first + ([m["id"] for m in listed] if GATEWAY_MODELS else [])))
    out, names = [], set()
    for model in order:
        entry = model_entry(model, (by_id.get(model) or {}).get("context_length"), model in first)
        # the picker shows names; a second model of the same name keeps its gateway prefix
        if entry["name"] in names:
            entry["name"] = model
        names.add(entry["name"])
        out.append(entry)
    return out


def headers(server: dict) -> dict[str, str] | None:
    """Headers OpenHands sends to the server; None for a login Continue cannot make."""
    out = dict(server.get("headers") or {})
    auth = server.get("auth") or {}
    strategy = auth.get("strategy", "none") if isinstance(auth, dict) else "token"
    if strategy in ("api_key", "bearer") and auth.get("value"):
        name = auth.get("header_name") if strategy == "api_key" else None
        out[name or "Authorization"] = auth["value"] if name else "Bearer " + auth["value"]
    elif strategy == "header":
        out.update(auth.get("headers") or {})
    elif strategy not in ("none", "api_key", "bearer"):
        return None
    return {k: str(v) for k, v in out.items()}


def mcp_servers(config: dict) -> list[dict]:
    out = []
    for name, server in sorted(config.items()):
        if not isinstance(server, dict) or server.get("enabled") is False:
            continue
        if server.get("command"):
            entry = {"name": name, "command": server["command"], "args": server.get("args") or []}
            if server.get("env"):
                entry["env"] = {k: str(v) for k, v in server["env"].items()}
            out.append(entry)
            continue
        if not server.get("url"):
            continue
        sent = headers(server)
        if sent is None:
            continue
        entry = {"name": name, "url": server["url"],
                 "type": "sse" if server.get("transport") == "sse" else "streamable-http"}
        if sent:
            entry["requestOptions"] = {"headers": sent}
        out.append(entry)
    return out


def render(listed: list[dict], mcp: dict) -> str:
    config = {"name": "OpenHands", "version": "1.0.0", "schema": "v1",
              "models": models(listed), "mcpServers": mcp_servers(mcp)}
    return MARKER + "\n" + json.dumps(config, indent=2) + "\n"


def write_config(text: str) -> bool:
    """CONFIG gets text when it is ours or missing; True when it changed."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            have = f.read()
    except FileNotFoundError:
        have = None
    if have is not None and not have.startswith(MARKER):
        return False
    if have == text:
        return False
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    # MCP headers are credentials: readable by the pod's user only
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CONFIG)
    return True


def write_settings() -> None:
    """SETTINGS into the machine settings; keys there that SETTINGS does not name stay."""
    if not SETTINGS:
        return
    try:
        with open(MACHINE, encoding="utf-8") as f:
            have = json.load(f)
    except FileNotFoundError:
        have = {}
    except (OSError, ValueError):
        # comments or a user edit that is not plain JSON
        return
    if all(have.get(k) == v for k, v in SETTINGS.items()):
        return
    os.makedirs(os.path.dirname(MACHINE), exist_ok=True)
    tmp = MACHINE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({**have, **SETTINGS}, f, indent=2)
    os.replace(tmp, MACHINE)


def main() -> None:
    while True:
        try:
            mcp = agent_mcp()
            listed = gateway_models()
            # without the agent-server or the gateway the last good file stays
            if mcp is not None and (listed or MODELS):
                if write_config(render(listed, mcp)):
                    print(f"vscode-sync: {CONFIG} written", flush=True)
            write_settings()
        except Exception as exc:  # noqa: BLE001
            print(f"vscode-sync: {exc}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
