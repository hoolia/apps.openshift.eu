import httpx
import pytest

from accountant_agent.config import McpServer
from accountant_agent.mcp_client import McpClient, ToolRegistry, qualify

from conftest import TOOLS, FakeMcp, router


@pytest.mark.parametrize("sse", [False, True])
async def test_lists_all_pages_and_calls(sse, cfg):
    mcp = FakeMcp(TOOLS, sse=sse)
    async with httpx.AsyncClient(transport=router(mcp)) as http:
        client = McpClient(cfg.mcp_servers[0], http)
        tools = await client.list_tools()
        assert [t["name"] for t in tools] == [t["name"] for t in TOOLS]
        result = await client.call_tool("draft_booking", {"invoiceId": "INV-1"})
    assert result["content"][0]["text"] == "ok"
    assert mcp.calls == [("draft_booking", {"invoiceId": "INV-1"})]
    assert mcp.sessions == 1


async def test_sends_the_token_and_protocol_headers(cfg):
    seen = []
    mcp = FakeMcp(TOOLS)

    def handle(request):
        seen.append(dict(request.headers))
        return mcp.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await McpClient(cfg.mcp_servers[0], http).list_tools()
    assert all(h["authorization"] == "Bearer sa-token-1" for h in seen)
    assert "mcp-protocol-version" not in seen[0]
    assert seen[-1]["mcp-protocol-version"] == "2025-06-18"


async def test_expired_session_is_renewed(cfg):
    mcp = FakeMcp(TOOLS)
    async with httpx.AsyncClient(transport=router(mcp)) as http:
        client = McpClient(cfg.mcp_servers[0], http)
        await client.list_tools()
        mcp.expire_next = True
        await client.call_tool("list_purchase_invoices", {})
    assert mcp.sessions == 2


async def test_registry_caches_filters_and_skips_a_dead_server(cfg):
    mcp = FakeMcp(TOOLS)
    now = [0.0]
    servers = [*cfg.mcp_servers, McpServer("down", "http://down.test/mcp")]
    async with httpx.AsyncClient(transport=router(mcp)) as http:
        reg = ToolRegistry(servers, http, cache_seconds=60, clock=lambda: now[0])
        tools = await reg.discover()
        assert sorted(tools) == sorted(qualify("ledger", t["name"]) for t in TOOLS)
        sessions = mcp.sessions
        await reg.discover()
        now[0] = 61
        await reg.discover()
        assert set(await reg.for_role(["ledger__draft_*"])) == {"ledger__draft_booking"}
        assert len(await reg.for_role(())) == 3
    assert mcp.sessions == sessions


def test_qualify_makes_a_function_name():
    assert qualify("my.server", "a tool") == "my_server__a_tool"
    assert len(qualify("s" * 40, "t" * 40)) == 64


async def test_rpc_error_is_raised(cfg):
    from accountant_agent.mcp_client import McpError

    async with httpx.AsyncClient(transport=router(FakeMcp(TOOLS))) as http:
        with pytest.raises(McpError, match="no such method"):
            await McpClient(cfg.mcp_servers[0], http).request("prompts/list")
