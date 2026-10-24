# MCP 工具 + 长期上下文 Agent

首选接入已改为不依赖 LangChain 的 [MCP 工具绑定](tool-integration.md)，入口为 `examples/mcp_host.py`，支持多环境工具隔离、目录发现和描述覆盖。下文保留旧的 LangChain MCP 示例，适用于尚未启用宿主绑定的实例。

`examples/mcp_agent.py` 把三件事接在一起：

```text
MCP Server（stdio / HTTP）
        ↓ 工具
langchain-mcp-adapters → LangChain StructuredTool
        ↓
ContextAgent（本项目的 LangChain 适配器）
        ↓ 每次模型调用前 /compile
当前上下文实例（消息、状态字段、工具目录、调用结果）
```

MCP 只负责提供工具；Context Protocol 负责保存并编译上下文。两者都不执行模型，模型仍由宿主调用。

## 安装与运行

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-agent.txt
.\run.ps1
# 另一个终端：内置的 stdio MCP demo + 脚本模型，不产生模型费用
.venv/Scripts/python.exe -X utf8 -m examples.mcp_agent --mock --smoke-test
```

示例会：

1. 启动 `examples/mcp_server.py`（真实的 MCP stdio server，提供 `add` 和 `echo`）；
2. 用 `MultiServerMCPClient.get_tools()` 把 MCP 工具转成 LangChain 工具；
3. 创建/复用上下文实例，并把工具 Schema 注册进上下文；
4. 第一轮调用 MCP `add(6, 7)`，再用内置 `context_state_set` 保存 `last_result=13`；
5. 丢弃整个 LangChain Agent，用同一实例新建一个 Agent，第二轮直接从上下文读出 13，不重新调用工具。

真实模型去掉 `--mock`，并在 `.env` 配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`。

## 接入自己的 MCP Server

准备一个与 `MultiServerMCPClient` 兼容的 JSON：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/work"],
      "transport": "stdio"
    },
    "remote": {
      "url": "http://127.0.0.1:3000/mcp",
      "transport": "streamable_http"
    }
  }
}
```

```powershell
.venv/Scripts/python.exe -X utf8 -m examples.mcp_agent `
  --mcp-config mcp.json `
  --template TEMPLATE_ID `
  --instance researcher `
  --agent-id researcher `
  --interactive
```

同一 `--template` + `--instance` 每次启动都会连接同一个实例，保留消息、状态字段和工具记录。也可以用 `--session UUID` 续用已有实例。`--existing-only` 可以禁止拼错简名时自动新建。

## 长期对话的关键点

- **历史由上下文实例保存，不由 LangGraph checkpoint 保存。** 每轮重新 `compile`，所以换进程、换 Agent、换机器（连同一个数据库）都能继续。
- **默认对话组件按完整轮次保留最近 12 轮。** 超出部分仍在数据库里，但不会发给模型。要跨很多轮记住事实，应让 Agent 写入状态字段或记忆，而不是只依赖原始对话。
- **内置 `context_state_set` 是宿主侧工具。** 它调用 `state.set`，值会出现在下一次编译的状态组件中。数据库中工具名保持 `context.state.set`，LangChain 侧映射为 `context_state_set`。
- **MCP 工具是异步的。** `ContextAgent.arun()` 是 MCP 场景的入口；同步 `run()` 只适用于同步工具。异步 middleware 内部把 Context Protocol 的 HTTP 调用放到线程中，避免阻塞模型事件循环。
- **MCP 会话在 Agent 生命周期内保持。** 示例用 `client.session(server_name)` + `load_mcp_tools(session)`，每个 Server 只建立一个常驻会话；直接调用 `get_tools()` 会在每次工具调用时新建会话，不适合依赖服务端会话状态的 MCP Server。
- **MCP 工具目录在创建 Agent 时同步。** MCP Server 增加/删除工具后，重启宿主或重建 `ContextAgent` 即可；上下文里的旧工具记录不会被自动删除。
- **多个 MCP Server 的工具重名时**，用 `MultiServerMCPClient(..., tool_name_prefix=True)` 给工具加服务器前缀，避免上下文工具目录互相覆盖。
- **并发**：单个 `ContextAgent` 内部串行化轮次；多个宿主同时驱动同一个实例的完整轮次仍需要宿主侧互斥。本服务没有身份认证，保持只监听本机。

## 增加更多“上下文工具”

适配器不再自动提供 `context_state_set`，示例显式配置了 `ContextTools(context)`。状态和长期记忆可以统一通过 [上下文工具提供方](context-tools.md) 接入；也可像下面这样注册自己的普通 LangChain 记忆工具：

```python
from uuid import uuid4
from langchain_core.tools import StructuredTool
from context_protocol.models.commands import MemoryAppendCommand, AppendMemory

def remember(content: str) -> str:
    """把一条事实写入长期记忆。"""
    context.submit(MemoryAppendCommand(update_id=str(uuid4()), payload=AppendMemory(content=content)))
    return "remembered"

agent = ContextAgent(context, model, tools=mcp_tools + [StructuredTool.from_function(remember)])
```

默认 `memory` 组件已经绑定 `memories` 来源，所以写入后会在下一次编译进入模型输入。不要让模型直接编辑任意组件或工具 Schema；配置类操作仍由工作台或宿主管理。

## 边界

MCP 工具可以执行真实副作用；本示例不提供工具审批、沙箱或权限隔离。生产接入应限制 MCP Server 的命令、工作目录和网络访问，并把上下文服务保持在本机或受控网络内。
