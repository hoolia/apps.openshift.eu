"""The A2A JSON-RPC surface end to end, with the official a2a-sdk server and fakes behind it."""

import httpx
from a2a.server.tasks import InMemoryTaskStore

from accountant_agent.server import async_url, build_apps

from conftest import TOOLS, FakeMcp, FakeModel, answer, router, tool_call
from test_agent import NEEDS_APPROVAL


def send(text, task_id=None, context_id=None, blocking=True):
    msg = {"kind": "message", "role": "user", "messageId": f"m-{text[:8]}-{task_id}", "parts": [{"kind": "text", "text": text}]}
    if task_id:
        msg["taskId"] = task_id
        msg["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": "1", "method": "message/send",
            "params": {"message": msg, "configuration": {"blocking": blocking}}}


async def client_for(cfg, mcp, model, role="bookkeeper"):
    http = httpx.AsyncClient(transport=router(mcp, model))
    apps = dict((r.name, app) for r, app in build_apps(cfg, InMemoryTaskStore(), http))
    a2a = httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[role]), base_url="http://acct.test")
    return http, a2a, apps


async def test_card_paths_and_readyz(cfg):
    http, a2a, apps = await client_for(cfg, FakeMcp(TOOLS), FakeModel([]))
    async with http, a2a:
        assert set(apps) == {"bookkeeper", "cfo"}
        for path in ("/.well-known/agent.json", "/.well-known/agent-card.json"):
            card = (await a2a.get(path)).json()
            assert card["name"] == "acct-bookkeeper"
            assert card["url"] == "http://acct.test:8081/"
            assert card["protocolVersion"] == "0.3.0"
            assert card["preferredTransport"] == "JSONRPC"
        r = await a2a.get("/readyz")
        assert (r.status_code, r.json()) == (200, {"ready": True})


async def test_approval_round_trip_on_one_task(cfg):
    def commit_on_answer(body):
        last = body["messages"][-1]
        assert last == {"role": "user", "content": "boek D-1 tok-123"}
        assert any("boek D-1" in (m.get("content") or "") for m in body["messages"] if m["role"] == "assistant")
        return tool_call("ledger__commit_booking", {"draftId": "D-1", "answer": "boek D-1 tok-123"}, "c2")

    mcp = FakeMcp(TOOLS, results={"draft_booking": NEEDS_APPROVAL})
    model = FakeModel([tool_call("ledger__draft_booking", {"invoiceId": "INV-7"}), commit_on_answer, answer("Booked D-1.")])
    http, a2a, _ = await client_for(cfg, mcp, model)
    async with http, a2a:
        first = (await a2a.post("/", json=send("Do the daily run."))).json()["result"]
        assert first["status"]["state"] == "input-required"
        parts = first["status"]["message"]["parts"]
        assert "boek D-1" in parts[0]["text"]
        assert parts[1]["data"]["draftId"] == "D-1"

        second = (await a2a.post("/", json=send("boek D-1 tok-123", first["id"], first["contextId"]))).json()["result"]
    assert second["id"] == first["id"]
    assert second["status"]["state"] == "completed"
    assert second["artifacts"][0]["parts"][0]["text"] == "Booked D-1."
    assert mcp.calls[-1] == ("commit_booking", {"draftId": "D-1", "answer": "boek D-1 tok-123"})


async def test_model_failure_fails_the_task(cfg):
    def broken(_):
        raise RuntimeError("synthetic")

    http, a2a, _ = await client_for(cfg, FakeMcp(TOOLS), FakeModel([broken]), role="cfo")
    async with http, a2a:
        res = (await a2a.post("/", json=send("Weekly report"))).json()["result"]
    assert res["status"]["state"] == "failed"
    assert "synthetic" in res["status"]["message"]["parts"][0]["text"]


async def test_without_a_model_not_ready_and_tasks_fail(raw_config):
    from accountant_agent import config

    cfg = config.parse({**raw_config, "model": {}})
    http, a2a, _ = await client_for(cfg, FakeMcp(TOOLS), FakeModel([]), role="cfo")
    async with http, a2a:
        assert (await a2a.get("/readyz")).status_code == 503
        res = (await a2a.post("/", json=send("Weekly report"))).json()["result"]
    assert res["status"]["state"] == "failed"
    assert "model.baseUrl is not set" in res["status"]["message"]["parts"][0]["text"]


def test_async_url():
    assert async_url("postgresql://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert async_url("postgres://h/db") == "postgresql+asyncpg://h/db"
    assert async_url("sqlite+aiosqlite:///x") == "sqlite+aiosqlite:///x"


async def test_database_task_store_and_readyz(cfg, tmp_path, monkeypatch):
    from accountant_agent.server import task_store

    monkeypatch.setenv("TASK_STORE_URL", f"sqlite+aiosqlite:///{tmp_path}/tasks.db")
    store, engine = await task_store(cfg)
    http = httpx.AsyncClient(transport=router(FakeMcp(TOOLS), FakeModel([answer("Weekly: fine.")])))
    apps = dict((r.name, app) for r, app in build_apps(cfg, store, http, engine))
    a2a = httpx.AsyncClient(transport=httpx.ASGITransport(app=apps["cfo"]), base_url="http://acct.test")
    async with http, a2a:
        assert (await a2a.get("/readyz")).status_code == 200
        res = (await a2a.post("/", json=send("Weekly report"))).json()["result"]
        assert (await store.get(res["id"])).status.state.value == "completed"
    await engine.dispose()

    from sqlalchemy.ext.asyncio import create_async_engine

    away = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing/dir/tasks.db")
    apps = dict((r.name, app) for r, app in build_apps(cfg, store, http, away))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=apps["cfo"]), base_url="http://acct.test") as a2a:
        assert (await a2a.get("/readyz")).status_code == 503
    await away.dispose()
