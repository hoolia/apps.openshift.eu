"""Serves every enabled role on its own port: one agent card and A2A endpoint per role."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskStore

from . import config as config_mod
from .agent import AccountantAgent
from .executor import RoleExecutor
from .mcp_client import ToolRegistry
from .model import ChatModel
from .transport import TRANSPORTS, agent_card

log = logging.getLogger("accountant_agent")


def async_url(url: str) -> str:
    """A CloudNativePG uri (postgresql://) as an SQLAlchemy asyncpg URL."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


async def task_store(cfg: config_mod.Config) -> tuple[TaskStore, object]:
    url = os.environ.get(cfg.task_store_url_env, "")
    if not url:
        log.info("task store: in memory")
        return InMemoryTaskStore(), None
    from a2a.server.tasks import DatabaseTaskStore
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(async_url(url), pool_pre_ping=True)
    store = DatabaseTaskStore(engine, create_table=True, table_name="a2a_tasks")
    await store.initialize()
    log.info("task store: database, table a2a_tasks")
    return store, engine


def build_apps(cfg: config_mod.Config, store: TaskStore, http: httpx.AsyncClient, engine=None, transport="jsonrpc"):
    registry = ToolRegistry(cfg.mcp_servers, http, cfg.tool_cache_seconds)
    model = ChatModel(cfg.model, http)
    wire = TRANSPORTS[transport]()

    async def ready() -> bool:
        if not cfg.model.base_url:
            return False
        if engine is None:
            return True
        from sqlalchemy import text

        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # not ready while the database is away
            log.warning("readyz: database: %s", exc)
            return False

    apps = []
    for role in cfg.roles:
        handler = DefaultRequestHandler(RoleExecutor(AccountantAgent(cfg, role, model, registry)), store)
        apps.append((role, wire.app(agent_card(cfg, role, wire.name), handler, ready)))
    return apps


async def serve(cfg: config_mod.Config) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        store, engine = await task_store(cfg)
        servers = [
            uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=role.port, log_level="info"))
            for role, app in build_apps(cfg, store, http, engine)
        ]
        for role in cfg.roles:
            log.info("role %s on port %d", role.name, role.port)
        await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(serve(config_mod.load()))


if __name__ == "__main__":
    main()
