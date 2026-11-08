"""Agent configuration: one JSON file, written by the chart into a ConfigMap."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = "/etc/accountant-agent/config.json"


@dataclass(frozen=True)
class Role:
    name: str
    port: int
    description: str
    instructions: str
    # fnmatch patterns of the tool names (<server>__<tool>) the role may call; empty: all
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Model:
    # empty: not configured; the agent is not ready and fails every task
    base_url: str
    name: str
    # file with a bearer token, read on every call (a projected ServiceAccount token)
    token_file: str = ""
    # environment variable with an API key, used when token_file is empty
    api_key_env: str = ""
    temperature: float = 0.0
    max_tool_rounds: int = 20
    timeout: float = 300.0


@dataclass(frozen=True)
class McpServer:
    name: str
    url: str
    token_file: str = ""


@dataclass(frozen=True)
class Config:
    agent_name: str
    public_url: str
    roles: tuple[Role, ...]
    model: Model
    mcp_servers: tuple[McpServer, ...] = ()
    # seconds a discovered tool list is reused
    tool_cache_seconds: int = 300
    # environment variable with the task store database URL; empty value: in memory
    task_store_url_env: str = "TASK_STORE_URL"
    # words of the human's answer to a draft: "<approve> <id>" or "<reject> <id> <reason>"
    approve_command: str = "boek"
    reject_command: str = "afkeuren"

    def role(self, name: str) -> Role:
        for r in self.roles:
            if r.name == name:
                return r
        raise KeyError(name)


def _role(name: str, raw: dict) -> Role:
    return Role(
        name=name,
        port=int(raw["port"]),
        description=str(raw.get("description") or name),
        instructions=str(raw.get("instructions") or ""),
        tools=tuple(raw.get("tools") or ()),
    )


def parse(raw: dict) -> Config:
    model = raw.get("model") or {}
    roles = tuple(
        _role(name, r)
        for name, r in sorted((raw.get("roles") or {}).items())
        if r.get("enabled", True)
    )
    if not roles:
        raise ValueError("no role is enabled")
    ports = [r.port for r in roles]
    if len(set(ports)) != len(ports):
        raise ValueError("two roles share a port")
    return Config(
        agent_name=str(raw.get("agentName") or "accountant"),
        public_url=str(raw.get("publicUrl") or "http://localhost:{port}/"),
        roles=roles,
        model=Model(
            base_url=str(model.get("baseUrl") or "").rstrip("/"),
            name=str(model.get("name") or ""),
            token_file=str(model.get("tokenFile") or ""),
            api_key_env=str(model.get("apiKeyEnv") or ""),
            temperature=float(model.get("temperature", 0.0)),
            max_tool_rounds=int(model.get("maxToolRounds", 20)),
            timeout=float(model.get("timeout", 300)),
        ),
        mcp_servers=tuple(
            McpServer(name=str(s["name"]), url=str(s["url"]), token_file=str(s.get("tokenFile") or ""))
            for s in raw.get("mcpServers") or ()
        ),
        tool_cache_seconds=int(raw.get("toolCacheSeconds", 300)),
        task_store_url_env=str(raw.get("taskStoreUrlEnv") or "TASK_STORE_URL"),
        approve_command=str((raw.get("approval") or {}).get("approveCommand") or "boek"),
        reject_command=str((raw.get("approval") or {}).get("rejectCommand") or "afkeuren"),
    )


def load(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("ACCOUNTANT_CONFIG") or DEFAULT_PATH)
    return parse(json.loads(p.read_text()))


def bearer(token_file: str, api_key_env: str = "") -> str:
    """The bearer token of a call: the file's content, re-read each time, else the env key."""
    if token_file:
        return Path(token_file).read_text().strip()
    if api_key_env:
        return os.environ.get(api_key_env, "")
    return ""
