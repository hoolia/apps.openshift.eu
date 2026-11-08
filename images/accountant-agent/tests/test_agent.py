import json

import httpx

from accountant_agent.agent import REQUEST_APPROVAL, AccountantAgent, approval_of
from accountant_agent.mcp_client import ToolRegistry
from accountant_agent.model import ChatModel

from conftest import TOOLS, FakeMcp, FakeModel, answer, router, tool_call

NEEDS_APPROVAL = {"structuredContent": {"approvalRequired": True, "draftId": "D-1",
                                        "summary": "Supplier A, 121.00 EUR, 4100 office", "tier": "2"},
                  "content": [{"type": "text", "text": "draft D-1 waits for approval"}]}


async def run(cfg, role, script, results=None, history=None):
    mcp = FakeMcp(TOOLS, results=results)
    model = FakeModel(script)
    async with httpx.AsyncClient(transport=router(mcp, model)) as http:
        agent = AccountantAgent(cfg, cfg.role(role), ChatModel(cfg.model, http), ToolRegistry(cfg.mcp_servers, http))
        out = await agent.run(history or [{"role": "user", "content": "Do the daily run."}])
    return out, mcp, model


async def test_plain_answer(cfg):
    out, _, model = await run(cfg, "cfo", [answer("Cash is fine.")])
    assert (out.state, out.text) == ("completed", "Cash is fine.")
    body, auth = model.requests[0]
    assert body["model"] == "test-model"
    assert auth == "Bearer sa-token-1"
    assert body["messages"][0]["role"] == "system"
    assert "boek <id>" in body["messages"][0]["content"]
    names = {t["function"]["name"] for t in body["tools"]}
    assert REQUEST_APPROVAL in names and "ledger__commit_booking" in names


async def test_tool_round_then_answer(cfg):
    out, mcp, model = await run(cfg, "bookkeeper", [
        tool_call("ledger__list_purchase_invoices", {}), answer("Nothing new.")])
    assert out.state == "completed"
    assert mcp.calls == [("list_purchase_invoices", {})]
    tool_msg = model.requests[1][0]["messages"][-1]
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


async def test_write_tool_asking_approval_stops_with_input_required(cfg):
    out, _, model = await run(cfg, "bookkeeper", [tool_call("ledger__draft_booking", {"invoiceId": "INV-7"})],
                              results={"draft_booking": NEEDS_APPROVAL})
    assert out.state == "input-required"
    assert "Supplier A, 121.00 EUR" in out.text
    assert "boek D-1" in out.text and "afkeuren D-1 <reason>" in out.text
    assert out.approvals[0].data() == {"type": "approval", "draftId": "D-1", "summary": "Supplier A, 121.00 EUR, 4100 office",
                                       "tier": "2", "tool": "ledger__draft_booking"}
    assert len(model.requests) == 1


async def test_request_approval_tool(cfg):
    out, mcp, _ = await run(cfg, "bookkeeper", [tool_call(REQUEST_APPROVAL, {"draftId": "D-9", "summary": "Rent"})])
    assert out.state == "input-required"
    assert "Draft D-9: Rent" in out.text
    assert mcp.calls == []


async def test_role_filter_hides_tools(cfg):
    out, mcp, model = await run(cfg, "bookkeeper", [tool_call("other__x", {}), answer("done")])
    assert out.state == "completed"
    assert model.requests[1][0]["messages"][-1]["content"] == "ERROR: unknown tool other__x"


async def test_rounds_are_bounded(cfg):
    out, _, _ = await run(cfg, "cfo", [tool_call("ledger__list_purchase_invoices", {}, f"c{i}") for i in range(4)])
    assert out.state == "failed"


def test_approval_of_text_json_and_negatives():
    text = {"content": [{"type": "text", "text": json.dumps({"approvalRequired": True, "id": "D-2", "summary": "s"})}]}
    assert approval_of(text).draft_id == "D-2"
    assert approval_of({"content": [{"type": "text", "text": "plain"}]}) is None
    assert approval_of({"structuredContent": {"approvalRequired": "yes", "draftId": "x"}}) is None
    assert approval_of({"structuredContent": {"approvalRequired": True}}) is None
