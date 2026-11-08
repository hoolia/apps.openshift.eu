"""Chat completions at an OpenAI-compatible endpoint, such as an LLM gateway."""

from __future__ import annotations

import httpx

from .config import Model, bearer


class ModelError(Exception):
    pass


class ChatModel:
    def __init__(self, cfg: Model, http: httpx.AsyncClient):
        self.cfg = cfg
        self.http = http

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        """One assistant message: {"content": str|None, "tool_calls": [...]}."""
        if not self.cfg.base_url:
            raise ModelError("model.baseUrl is not set")
        body: dict = {"messages": messages, "temperature": self.cfg.temperature}
        if self.cfg.name:
            body["model"] = self.cfg.name
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {}
        token = bearer(self.cfg.token_file, self.cfg.api_key_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await self.http.post(
            f"{self.cfg.base_url}/chat/completions", json=body, headers=headers, timeout=self.cfg.timeout
        )
        if resp.status_code >= 400:
            raise ModelError(f"model: HTTP {resp.status_code}: {resp.text[:300]}")
        choices = resp.json().get("choices") or []
        if not choices:
            raise ModelError("model: no choice in the answer")
        return choices[0].get("message") or {}
