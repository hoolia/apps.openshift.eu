"""The A2A transport of a role, behind one interface.

The executor, the request handler and the task store know nothing of the wire. JSON-RPC
over HTTP is the transport kagent 0.x speaks to a BYO agent; a gRPC transport is another
implementation of Transport around the same request handler (a2a-sdk GrpcHandler).
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import RequestHandler
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Config, Role

Ready = Callable[[], Awaitable[bool]]


def agent_card(cfg: Config, role: Role, transport: str = "JSONRPC") -> AgentCard:
    return AgentCard(
        name=f"{cfg.agent_name}-{role.name}",
        description=role.description,
        url=cfg.public_url.format(port=role.port, role=role.name),
        version="0.1.0",
        protocol_version="0.3.0",
        preferred_transport=transport,
        capabilities=AgentCapabilities(streaming=True, push_notifications=False, state_transition_history=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id=role.name,
                name=role.name,
                description=role.description,
                tags=["accounting", role.name],
            )
        ],
    )


class Transport(Protocol):
    name: str

    def app(self, card: AgentCard, handler: RequestHandler, ready: Ready): ...


class JsonRpcTransport:
    """JSON-RPC 2.0 over HTTP; the card at /.well-known/agent-card.json and agent.json."""

    name = "JSONRPC"

    def app(self, card: AgentCard, handler: RequestHandler, ready: Ready):
        async def readyz(_: Request):
            ok = await ready()
            return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

        async def healthz(_: Request):
            return JSONResponse({"ok": True})

        return A2AStarletteApplication(agent_card=card, http_handler=handler).build(
            routes=[Route("/readyz", readyz), Route("/healthz", healthz)]
        )


TRANSPORTS: dict[str, type] = {"jsonrpc": JsonRpcTransport}
