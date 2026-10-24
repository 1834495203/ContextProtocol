"""A long-running LangChain agent whose tools come from MCP.

The Context Protocol instance is the conversation store; every model call pulls
the compiled context and the MCP tool schemas registered from the current host.

Run:
  python -m examples.mcp_agent --mock --smoke-test
  python -m examples.mcp_agent --mcp-config mcp.json --template TEMPLATE_ID --instance researcher
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from pydantic import PrivateAttr

from context_protocol.adapters.langchain import ContextAgent
from context_protocol.client import ContextClient
from context_protocol.providers.context import ContextTools

ROOT = Path(__file__).resolve().parent.parent


def text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def content_text(value: Any) -> str:
    """MCP tools return LangChain content blocks; the demo needs their text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, list):
        return "".join(block.get("text", "") for block in value if isinstance(block, dict))
    return text(value)


class ScriptedModel(BaseChatModel):
    """Deterministic model for the integration test; it is not an LLM."""

    _requests: list = PrivateAttr(default_factory=list)
    _tool_requests: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self):
        return "context-protocol-mcp-scripted-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._requests.append(messages)
        self._tool_requests.append(kwargs.get("tools", []))
        last = messages[-1]
        if isinstance(last, HumanMessage) and "上一轮" in last.content:
            answer = AIMessage(content=f"上一轮保存的 last_result = {self.recall(messages)}。")
        elif isinstance(last, ToolMessage) and last.name == "add":
            value = json.loads(content_text(last.content))
            answer = AIMessage(content="", tool_calls=[{"id": str(uuid4()), "name": "context_state_set",
                "args": {"key": "last_result", "value": value, "value_type": "number",
                         "description": "MCP add 的结果"}, "type": "tool_call"}])
        elif isinstance(last, ToolMessage) and last.name == "context_state_set":
            answer = AIMessage(content="MCP add 返回 13，已保存到 last_result。")
        else:
            answer = AIMessage(content="", tool_calls=[{"id": str(uuid4()), "name": "add",
                "args": {"a": 6, "b": 7}, "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=answer)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)

    @staticmethod
    def recall(messages) -> Any:
        data = [m for m in messages if isinstance(m, HumanMessage) and m.content.startswith("以下是上下文数据")]
        groups = [g for m in data for g in json.loads(m.content.split("<context-data>\n", 1)[1].split("\n</context-data>", 1)[0])]
        state_text = next((g["text"] for g in groups if g["group_id"] == "state"), "")
        fields = [json.loads(part) for part in state_text.split("\n\n") if part.strip()]
        return next((f["value"] for f in fields if f.get("key") == "last_result"), "未找到")


def make_model(mock=False):
    if mock:
        return ScriptedModel()
    from langchain_openai import ChatOpenAI
    key, model = os.getenv("OPENAI_API_KEY"), os.getenv("LLM_MODEL")
    if not key or not model:
        raise ValueError("Set OPENAI_API_KEY and LLM_MODEL in .env, or use --mock.")
    return ChatOpenAI(model=model, api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None,
                      timeout=60, max_retries=0)


def mcp_config(path: str | None) -> dict:
    if path:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return document.get("mcpServers", document)
    return {"demo": {"command": sys.executable, "args": ["-m", "examples.mcp_server"],
                     "transport": "stdio", "cwd": str(ROOT)}}


def resolve_instance(args, http):
    if args.session and (args.template or args.agent_id or args.instance):
        raise SystemExit("--session resumes an instance; use --template and --agent-id to create a new one")
    if args.instance and not args.template:
        raise SystemExit("--instance requires --template")
    if args.existing_only and not args.instance:
        raise SystemExit("--existing-only requires --instance")
    if args.session:
        return args.session
    if args.instance:
        return ContextClient.connect(http, args.template, args.instance, agent_id=args.agent_id,
                                     name=args.instance, create=not args.existing_only).session_id
    response = http.post("/sessions", json={"name": "MCP Agent", "template_id": args.template,
                                            "agent_id": args.agent_id})
    response.raise_for_status()
    return response.json()["id"]


async def run(args):
    load_dotenv(ROOT / ".env")
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

    connections = mcp_config(args.mcp_config)
    client = MultiServerMCPClient(connections)
    # Keep one MCP session per server for the whole agent lifetime. get_tools()
    # opens a new session for every call, which is fine for a demo but not for
    # a long-running agent with server-side state.
    async with AsyncExitStack() as mcp_stack:
        tools = []
        for name in connections:
            session = await mcp_stack.enter_async_context(client.session(name))
            tools.extend(await load_mcp_tools(session, server_name=name,
                                              tool_name_prefix=client.tool_name_prefix))
        await run_agent(args, tools)


async def run_agent(args, tools):
    if args.list_tools:
        print(json.dumps([{"name": tool.name, "description": tool.description,
                           "input_schema": tool.args_schema} for tool in tools],
                         ensure_ascii=False, indent=2, default=str))
        return
    if not tools:
        raise SystemExit("The MCP server exposed no tools.")

    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=30, trust_env=False) as http:
        http.get("/health").raise_for_status()
        sid = resolve_instance(args, http)
        print(f"Context instance: {sid}\nMCP tools: {', '.join(tool.name for tool in tools)}", flush=True)
        context = ContextClient(http, sid)
        starting_calls = len(context.state()["state"]["calls"])
        agent = ContextAgent(context, make_model(args.mock), tools=tools,
                             tool_providers={'context': ContextTools(context).tools},
                             instructions="你是中文助手。外部工具由 MCP 提供；需要记住信息时使用 context_state_set。")
        print("Assistant:", await agent.arun(args.prompt), flush=True)

        if args.smoke_test:
            fresh = ContextAgent(context, make_model(args.mock), tools=tools,
                                 tool_providers={'context': ContextTools(context).tools},
                                 instructions="你是中文助手。外部工具由 MCP 提供；需要记住信息时使用 context_state_set。")
            recall = await fresh.arun("上一轮保存的 last_result 是多少？不要调用工具，直接读取上下文回答。")
            print("Fresh agent, same context:", recall, flush=True)
            assert "13" in text(recall), "The fresh agent did not recall the expected result"
            state = context.state()
            assert state["state"]["fields"].get("last_result", {}).get("value") == 13
            calls = [c for c in state["state"]["calls"].values() if c["seq"] > starting_calls]
            assert any(c["name"] == "add" and c["status"] == "completed" for c in calls)
            assert any(c["name"] == "context.state.set" and c["status"] == "completed" for c in calls)
            assert len(state["state"]["calls"]) == starting_calls + 2, "The fresh agent called tools instead of recalling context"
            print(f"Verified: MCP add -> 13; last_result persisted; revision={state['revision']}", flush=True)

        if args.interactive:
            while True:
                try:
                    prompt = (await asyncio.to_thread(input, "You (exit to quit): ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if prompt.lower() in {"exit", "quit"}:
                    break
                if prompt:
                    print("Assistant:", await agent.arun(prompt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8010/api")
    parser.add_argument("--mcp-config", help="JSON file with MultiServerMCPClient connections or mcpServers")
    parser.add_argument("--list-tools", action="store_true", help="List MCP tools without creating an agent")
    parser.add_argument("--template", help="Template ID used to create a new independent context")
    parser.add_argument("--instance", help="Stable instance short name within --template")
    parser.add_argument("--existing-only", action="store_true", help="Never create a missing instance")
    parser.add_argument("--session", help="Resume a persisted context by its UUID")
    parser.add_argument("--agent-id", help="Optional host Agent label")
    parser.add_argument("--mock", action="store_true", help="Scripted model; no provider/network call")
    parser.add_argument("--smoke-test", action="store_true", help="Two turns, replacing the agent between turns")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--prompt", default="请调用 MCP 的 add 工具计算 6 加 7，再把结果保存到 last_result。用中文回答。")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
