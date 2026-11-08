"""The tool loop of one role, independent of the A2A transport.

The model answers, or calls MCP tools until it answers. A booking is never committed by
the agent on its own: a write tool of the accounting MCP server answers that it needs an
approval, or the model calls the built-in tool request_approval with a draft. Either way
the run stops with state input-required and a short draft summary that names the commands
the human answers with. The approval token itself is checked by the MCP server, not here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import Config, Role
from .mcp_client import McpError, ToolRegistry
from .model import ChatModel

log = logging.getLogger(__name__)

REQUEST_APPROVAL = "request_approval"

REQUEST_APPROVAL_TOOL = {
    "type": "function",
    "function": {
        "name": REQUEST_APPROVAL,
        "description": (
            "Stop and ask a human to approve or reject a draft before it is booked. "
            "Call it once per draft that needs approval, with the id of the draft."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "draftId": {"type": "string", "description": "id of the draft"},
                "summary": {"type": "string", "description": "a few lines: what, who, amount, why"},
            },
            "required": ["draftId", "summary"],
        },
    },
}


@dataclass(frozen=True)
class Approval:
    draft_id: str
    summary: str
    tier: str = ""
    tool: str = ""

    def data(self) -> dict:
        d = {"type": "approval", "draftId": self.draft_id, "summary": self.summary}
        if self.tier:
            d["tier"] = self.tier
        if self.tool:
            d["tool"] = self.tool
        return d


@dataclass(frozen=True)
class Outcome:
    state: str  # completed | input-required | failed
    text: str
    approvals: tuple[Approval, ...] = ()


def approval_of(result: dict, tool: str = "") -> Approval | None:
    """An approval request in an MCP tool result: approvalRequired true in structuredContent,
    or in a JSON object in the first text content."""
    found = result.get("structuredContent")
    if not isinstance(found, dict):
        found = None
        for c in result.get("content") or ():
            if c.get("type") == "text":
                try:
                    found = json.loads(c.get("text") or "")
                except ValueError:
                    found = None
                break
    if not isinstance(found, dict) or found.get("approvalRequired") is not True:
        return None
    draft = str(found.get("draftId") or found.get("id") or "")
    if not draft:
        return None
    return Approval(draft, str(found.get("summary") or ""), str(found.get("tier") or ""), tool)


def result_text(result: dict) -> str:
    parts = [c.get("text", "") for c in result.get("content") or () if c.get("type") == "text"]
    if not parts and "structuredContent" in result:
        parts = [json.dumps(result["structuredContent"])]
    text = "\n".join(parts) or "(no content)"
    return f"ERROR: {text}" if result.get("isError") else text


def approval_message(approvals, approve: str, reject: str) -> str:
    lines = []
    for a in approvals:
        lines.append(f"Draft {a.draft_id}: {a.summary}".strip())
        lines.append(f"  {approve} {a.draft_id}  |  {reject} {a.draft_id} <reason>")
    return "Approval needed.\n" + "\n".join(lines)


class AccountantAgent:
    def __init__(self, cfg: Config, role: Role, model: ChatModel, tools: ToolRegistry):
        self.cfg = cfg
        self.role = role
        self.model = model
        self.tools = tools

    def system_prompt(self) -> str:
        return (
            f"{self.role.instructions}\n\n"
            "Never book, post or send anything without an approval. When a tool answers that "
            f"a draft needs approval, or before any booking, call {REQUEST_APPROVAL} with the "
            "draft id and a summary of a few lines. A human answers with "
            f"'{self.cfg.approve_command} <id>' or '{self.cfg.reject_command} <id> <reason>'; "
            "pass that answer to the tool that commits or discards the draft."
        ).strip()

    async def run(self, history: list[dict]) -> Outcome:
        tools = await self.tools.for_role(self.role.tools)
        offered = [t.openai() for t in tools.values()] + [REQUEST_APPROVAL_TOOL]
        messages = [{"role": "system", "content": self.system_prompt()}, *history]
        for _ in range(self.cfg.model.max_tool_rounds):
            reply = await self.model.complete(messages, offered)
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content"), **({"tool_calls": calls} if calls else {})})
            if not calls:
                return Outcome("completed", reply.get("content") or "")
            approvals: list[Approval] = []
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                if name == REQUEST_APPROVAL:
                    approvals.append(Approval(str(args.get("draftId") or ""), str(args.get("summary") or "")))
                    content = "Waiting for the human's answer."
                elif name in tools:
                    try:
                        result = await self.tools.call(tools[name], args)
                    except McpError as exc:
                        result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
                    if a := approval_of(result, name):
                        approvals.append(a)
                    content = result_text(result)
                else:
                    content = f"ERROR: unknown tool {name}"
                messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": content})
            approvals = [a for a in approvals if a.draft_id]
            if approvals:
                text = approval_message(approvals, self.cfg.approve_command, self.cfg.reject_command)
                return Outcome("input-required", text, tuple(approvals))
        return Outcome("failed", f"stopped after {self.cfg.model.max_tool_rounds} tool rounds")
