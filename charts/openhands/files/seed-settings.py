#!/usr/bin/env python3
"""Write the chart's model and MCP defaults into the agent-server settings.

Runs as the postStart hook once per change of those values (SEED_HASH); a later
edit in the UI is kept until the values change again. SEED_LLM is a JSON llm
diff, SEED_MCP a JSON map of server name to MCP server settings, where passEnv
names pod environment variables to hand to a stdio server. With gatewayModels,
profiles for new gateway models are added on every start; without a model, the
first gateway model named in preferredModels becomes the default. SEED_CONDENSER
is written into the settings and every OpenHands agent profile once per change
of its own value. Always exits 0 so a failed seed never kills the container.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:18000"
STATE = os.path.join(os.environ.get("HOME", "/tmp"), ".openhands", "agent-canvas")
MARKER = os.path.join(STATE, ".seeded-" + os.environ.get("SEED_HASH", "none"))


def request(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Session-API-Key": os.environ.get("LOCAL_BACKEND_API_KEY", ""),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        return err.code, None
    except (OSError, ValueError):
        return 0, None


def call(method: str, path: str, body: dict | None = None) -> int:
    return request(method, path, body)[0]


def seed_connection(llm: dict, conn: dict, models: list[str]) -> bool:
    """Provider connection the LLM settings page lists, one profile per model on it."""
    status, conns = request("GET", "/api/llm/provider-connections")
    if status != 200:
        return False
    body = {"display_name": conn["display_name"], "provider": conn["provider"],
            "base_url": llm["base_url"], "api_key": llm["api_key"]}
    found = [c for c in conns or [] if c.get("display_name") == conn["display_name"]]
    if found:
        status = call("PATCH", f"/api/llm/provider-connections/{found[0]['id']}", body)
    else:
        status = call("POST", "/api/llm/provider-connections", body)
    status, conns = request("GET", "/api/llm/provider-connections")
    found = [c for c in conns or [] if c.get("display_name") == conn["display_name"]]
    if not found:
        print("seed: provider connection not written", file=sys.stderr)
        return False
    conn_id = found[0]["id"]
    ok = True
    names = {}
    for model in dict.fromkeys([llm.get("model"), *models]):
        if not model:
            continue
        # profile names are path segments; the model keeps its gateway prefix
        names[model] = model.rsplit("/", 1)[-1]
        if call("POST", f"/api/profiles/{names[model]}",
                {"llm": {"model": model, "provider_connection_id": conn_id},
                 "include_secrets": False}) not in (200, 201):
            print(f"seed: profile {names[model]} not written", file=sys.stderr)
            ok = False
    if llm.get("model") in names:
        if call("POST", f"/api/profiles/{names[llm['model']]}/activate") != 200:
            ok = False
    # the agent-server turns the plain llm settings into a profile "default" without a
    # connection; the connection's profiles replace it
    status, listing = request("GET", "/api/profiles")
    active = (listing or {}).get("active_profile")
    for prof in (listing or {}).get("profiles", []):
        if (prof.get("name") == "default" and not prof.get("provider_connection_id")
                and prof.get("base_url") == llm["base_url"] and active and active != "default"):
            # agent profiles that use it move to the active one, or the delete is refused
            status, agents = request("GET", "/api/agent-profiles")
            for agent in (agents or {}).get("profiles", []):
                if agent.get("llm_profile_ref") != "default":
                    continue
                status, detail = request("GET", f"/api/agent-profiles/{agent['name']}")
                body = (detail or {}).get("profile")
                if not body or call("POST", f"/api/agent-profiles/{agent['name']}",
                                    {**body, "llm_profile_ref": active}) not in (200, 201):
                    print(f"seed: agent profile {agent['name']} not moved to {active}",
                          file=sys.stderr)
            if call("DELETE", "/api/profiles/default") not in (200, 204):
                print("seed: profile default not removed", file=sys.stderr)
    return ok


def gateway_models(llm: dict, conn: dict) -> list[str]:
    """Every model the gateway lists, in the form of llm.model (provider prefix)."""
    req = urllib.request.Request(llm["base_url"].rstrip("/") + "/models",
                                 headers={"Authorization": "Bearer " + llm.get("api_key", "")})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            listing = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        print(f"seed: gateway models not listed: {exc}", file=sys.stderr)
        return []
    return [f"{conn['provider']}/{m['id']}" for m in listing.get("data", []) if m.get("id")]


def preferred_model(llm: dict, conn: dict, preferred: list[str]) -> str | None:
    """The first model of preferred, by name after the last "/", that the gateway lists."""
    listed = {m.rsplit("/", 1)[-1]: m for m in gateway_models(llm, conn)}
    return next((listed[name] for name in preferred if name in listed), None)


def seed_condenser() -> None:
    """Condenser settings, once per value: the settings and each OpenHands agent profile."""
    raw = os.environ.get("SEED_CONDENSER") or ""
    if not raw:
        return
    marker = os.path.join(STATE, ".seeded-condenser-" + hashlib.sha256(raw.encode()).hexdigest()[:8])
    if os.path.exists(marker):
        return
    condenser = json.loads(raw)
    ok = call("PATCH", "/api/settings", {"agent_settings_diff": {"condenser": condenser}}) == 200
    status, agents = request("GET", "/api/agent-profiles")
    ok = ok and status == 200
    for agent in (agents or {}).get("profiles", []):
        if agent.get("agent_kind") != "openhands":
            continue
        status, detail = request("GET", f"/api/agent-profiles/{agent['name']}")
        body = (detail or {}).get("profile")
        if not body or call("POST", f"/api/agent-profiles/{agent['name']}",
                            {**body, "condenser": condenser}) not in (200, 201):
            print(f"seed: condenser of agent profile {agent['name']} not written", file=sys.stderr)
            ok = False
    if ok:
        os.makedirs(STATE, exist_ok=True)
        open(marker, "w").close()
    else:
        print("seed: condenser not written", file=sys.stderr)


def add_gateway_profiles(llm: dict, conn: dict) -> None:
    """Profiles for gateway models that have none yet; existing profiles stay as edited."""
    status, conns = request("GET", "/api/llm/provider-connections")
    found = [c for c in conns or [] if c.get("display_name") == conn["display_name"]]
    status, listing = request("GET", "/api/profiles")
    if not found or status != 200:
        return
    profiles = (listing or {}).get("profiles", [])
    taken = {p.get("name") for p in profiles}
    known = {p.get("model") for p in profiles}
    for model in gateway_models(llm, conn):
        if model in known:
            continue
        # profile names allow letters, digits, dot, dash and underscore only
        name = re.sub(r"[^A-Za-z0-9._-]", "-", model.rsplit("/", 1)[-1])
        if name in taken:
            name = re.sub(r"[^A-Za-z0-9._-]", "-", model.split("/", 1)[-1])
        if name in taken:
            continue
        if call("POST", f"/api/profiles/{name}",
                {"llm": {"model": model, "provider_connection_id": found[0]["id"]},
                 "include_secrets": False}) in (200, 201):
            taken.add(name)
        else:
            print(f"seed: profile {name} not written", file=sys.stderr)


def main() -> None:
    for _ in range(120):
        if call("GET", "/alive") == 200:
            break
        time.sleep(2)
    else:
        return
    llm = json.loads(os.environ.get("SEED_LLM") or "{}")
    conn = llm.pop("connection", None)
    models = llm.pop("models", [])
    every = llm.pop("gatewayModels", False)
    preferred = llm.pop("preferredModels", [])
    seed_condenser()
    if os.path.exists(MARKER):
        if every and conn and llm.get("base_url"):
            add_gateway_profiles(llm, conn)
        return
    ok = True
    if preferred and not llm.get("model") and conn and llm.get("base_url"):
        llm["model"] = preferred_model(llm, conn, preferred)
        if not llm["model"]:
            llm.pop("model")
            print("seed: the gateway lists none of preferredModels", file=sys.stderr)
    if llm and call("PATCH", "/api/settings", {"agent_settings_diff": {"llm": llm}}) != 200:
        ok = False
    if conn and llm.get("base_url") and not seed_connection(llm, conn, models):
        ok = False
    if every and conn and llm.get("base_url"):
        add_gateway_profiles(llm, conn)
    for name, server in json.loads(os.environ.get("SEED_MCP") or "{}").items():
        # the agent-server starts stdio servers with a reduced environment
        pass_env = server.pop("passEnv", [])
        if pass_env:
            server["env"] = {k: os.environ[k] for k in pass_env if k in os.environ}
        status = call("POST", f"/api/settings/mcp/{name}", server)
        if status not in (200, 201):
            status = call("PATCH", f"/api/settings/mcp/{name}", server)
        if status not in (200, 201):
            print(f"seed: MCP server {name} not written ({status})", file=sys.stderr)
            ok = False
    # environment of the ACP agents (Claude Code, Codex, Gemini CLI): the UI asks for
    # these names until the secrets store holds them
    for name, value in json.loads(os.environ.get("SEED_SECRETS") or "{}").items():
        if call("PUT", "/api/settings/secrets", {"name": name, "value": value,
                                                 "description": "set by the chart"}) not in (200, 201):
            print(f"seed: secret {name} not written", file=sys.stderr)
            ok = False
    skills = json.loads(os.environ.get("SEED_SKILLS") or "[]")
    if skills and call("PATCH", "/api/settings", {"agent_settings_diff": {
            "agent_context": {"registered_marketplaces": skills}}}) != 200:
        print("seed: skill marketplaces not written", file=sys.stderr)
        ok = False
    # selectable ACP agent profiles; the agent-server starts each CLI with its default command
    for name, prof in json.loads(os.environ.get("SEED_AGENT_PROFILES") or "{}").items():
        body = {"name": name, "agent_kind": "acp", "acp_command": None, "acp_args": None, **prof}
        if call("POST", f"/api/agent-profiles/{name}", body) not in (200, 201):
            print(f"seed: agent profile {name} not written", file=sys.stderr)
            ok = False
    if ok:
        os.makedirs(os.path.dirname(MARKER), exist_ok=True)
        open(MARKER, "w").close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"seed: {exc}", file=sys.stderr)
