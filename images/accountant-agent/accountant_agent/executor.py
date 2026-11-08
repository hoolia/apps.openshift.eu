"""A2A AgentExecutor of one role: task in, tool loop, task state out."""

from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Message, Part, Role, TextPart
from a2a.utils import new_task

from .agent import AccountantAgent

log = logging.getLogger(__name__)


def _text(message: Message) -> str:
    out = []
    for p in message.parts:
        root = p.root
        if isinstance(root, TextPart):
            out.append(root.text)
        elif isinstance(root, DataPart):
            out.append(json.dumps(root.data))
    return "\n".join(out)


def history_of(task, message: Message | None) -> list[dict]:
    """The task's conversation as chat messages, oldest first, each A2A message once."""
    seen: set[str] = set()
    ordered: list[Message] = []
    for m in [*(task.history or []), task.status.message if task.status else None, message]:
        if m is None or m.message_id in seen:
            continue
        seen.add(m.message_id)
        ordered.append(m)
    return [
        {"role": "user" if m.role == Role.user else "assistant", "content": _text(m)}
        for m in ordered
        if _text(m)
    ]


class RoleExecutor(AgentExecutor):
    def __init__(self, agent: AccountantAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        try:
            outcome = await self.agent.run(history_of(task, context.message))
        except Exception as exc:  # the task fails, the server stays up
            log.exception("task %s failed", task.id)
            await updater.failed(updater.new_agent_message([Part(root=TextPart(text=f"error: {exc}"))]))
            return
        text = Part(root=TextPart(text=outcome.text))
        if outcome.state == "completed":
            await updater.add_artifact([text], name="result")
            await updater.complete()
        elif outcome.state == "input-required":
            parts = [text, *(Part(root=DataPart(data=a.data())) for a in outcome.approvals)]
            await updater.requires_input(updater.new_agent_message(parts), final=True)
        else:
            await updater.failed(updater.new_agent_message([text]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
