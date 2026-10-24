"""Real MCP stdio tools + LangChain async middleware + HTTP API + storage."""
import asyncio

import pytest

pytest.importorskip("langchain_mcp_adapters")

from langchain_mcp_adapters.client import MultiServerMCPClient

from context_protocol.adapters.langchain import ContextAgent
from context_protocol.client import ContextClient
from context_protocol.providers.context import ContextTools
from examples.mcp_agent import ScriptedModel, mcp_config

INSTRUCTIONS = "你是中文助手。外部工具由 MCP 提供；需要记住信息时使用 context_state_set。"


async def two_turns(client):
    client.base_url = "http://testserver/api/"
    session = client.post("/sessions", json={"name": "mcp-agent-test"}).json()
    context = ContextClient(client, session["id"])
    tools = await MultiServerMCPClient(mcp_config(None)).get_tools()

    first = ContextAgent(context, ScriptedModel(), tools=tools, instructions=INSTRUCTIONS,
                         tool_providers={'context': ContextTools(context).tools})
    answer = await first.arun("调用 MCP 的 add 计算 6 加 7，并保存 last_result。")

    # A fresh agent has no LangGraph state; it must rebuild from the context.
    second = ContextAgent(context, ScriptedModel(), tools=tools, instructions=INSTRUCTIONS,
                          tool_providers={'context': ContextTools(context).tools})
    recall = await second.arun("上一轮保存的 last_result 是多少？不要调用工具。")
    return context, answer, recall


def test_mcp_tools_are_compiled_and_state_survives_a_fresh_agent(client):
    context, answer, recall = asyncio.run(two_turns(client))
    assert "13" in answer
    assert "13" in recall
    state = context.state()["state"]
    assert state["fields"]["last_result"]["value"] == 13
    assert {c["name"] for c in state["calls"].values()} == {"add", "context.state.set"}
    assert all(c["status"] == "completed" for c in state["calls"].values())
    tools = {tool["name"] for tool in context.build()["model_input"]["tools"]}
    assert {"add", "echo"} <= tools
