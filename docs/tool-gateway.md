# 服务端集中工具接入（推荐）

工具统一注册到上下文 Web 服务，模板或实例选择来源，Agent 只连接上下文实例。Agent 保留模型调用与循环控制；服务端负责工具连接、发现、描述合并、执行与结果记录。核心编译器仍然独立，原有 HostTools / ContextAgent 宿主直连接口保留兼容。

```text
MCP stdio / Streamable HTTP ─┐
服务器本地方法 ──────────────┼→ 工具网关 → 上下文编译 → Agent → 模型
状态、记忆能力 ──────────────┘      ↑                  │
                               └──── tools/call ─────┘
```

## 1. 管理员添加来源

`POST /api/tool-sources`：

```json
{
  "id": "documents",
  "title": "项目文档",
  "connection": {
    "transport": "stdio",
    "command": "python",
    "args": ["my_documents_server.py"],
    "cwd": "D:/my-tools"
  }
}
```

支持四种 connection：

| transport | 配置 | 执行位置 |
| --- | --- | --- |
| stdio | command、args、cwd、env_refs | 服务端启动的 MCP 子进程 |
| streamable_http | url、header_env_refs | 远程 MCP Server |
| context | tools，例如 state.set / memory.append | 当前上下文实例的状态、记忆提供方 |
| local | registry_key | Web 服务启动时注册的 Python 方法 |

来源持久保存在独立 tool_sources 表，新表通过启动初始化创建，不重建已有上下文数据。更新已有来源必须传 expected_revision；修改、停用或删除会关闭相关运行连接，使旧准备结果失效。

`env_refs` 和 `header_env_refs` 的值是服务器环境变量名。例如 `{"Authorization":"DOCUMENTS_AUTHORIZATION"}`，真实头部值从服务器环境读取；不要把真实凭据写到实例或提示词里。

`GET /api/tool-sources` / `GET /api/tool-sources/{id}` 查看配置和缓存目录。`POST /api/tool-sources/{id}/discover` 独立发现工具，无需调用模型。缓存的 availability 始终是 unconfirmed，不表示当前实例具备执行能力；发现失败保留缓存并记录错误类型。

工作台新增“工具接入”页面，可添加来源、发现目录、编辑和移除。

## 2. 模板或实例选择来源

向模板或实例的 `/updates` 提交：

```json
{
  "type": "tool.sources.set",
  "update_id": "unique-update-id",
  "payload": {"sources": ["documents", "context"]}
}
```

模板级选择会同步到已有实例；实例级选择只影响当前实例。sources=[] 明确启用集中模式但不提供工具；sources=null 恢复宿主直连模式。来源配置是全局管理数据，模板只保存来源 ID，不包含函数、连接对象或凭据。

原有 tool.prompt.set 配置照常生效，稳定身份仍为 provider_id/tool_name。必需工具缺失或被策略排除时暂停模型调用；连接或发现未知的来源会使准备结果不就绪。移除当前工具不会删除历史调用。

## 3. Agent 只获取输入、提交调用

先记录用户发言，然后调用 `POST /api/sessions/{id}/prepare`，正文可以为 `{}`。已选择集中来源时，服务自动建立独立实例连接、发现工具并编译：

```json
{
  "ready": true,
  "execution_mode": "gateway",
  "preparation_id": "opaque-id",
  "valid_until": "...",
  "compiled": {"model_input": {"messages": [], "tools": []}}
}
```

检查 ready 后，把 model_input 交给模型。记录模型实际产生的助手消息（message.append，包含 tool_call_ids），保存返回 update.seq，然后提交：

```json
{
  "preparation_id": "opaque-id",
  "call_id": "model-call-id",
  "name": "name-from-model-input",
  "arguments": {"query": "compiler"},
  "assistant_seq": 12,
  "turn_id": "same-turn-as-assistant"
}
```

发送到 `POST /api/sessions/{id}/tools/call`。服务校验准备结果、实例、当前来源修订、工具选择、Schema 和助手消息关联，然后保存 tool.call、执行工具、保存 tool.result。Agent 不再额外写这两条记录。

响应包含 call_id、output、is_error、duplicate、revision。MCP 输出保留完整结果结构；本地方法输出保持原 JSON 值。继续 prepare 即可让模型读到工具结果。无工具调用时保存最终助手消息，结束本轮。

准备标识有效五分钟，服务最多保留 1024 份有效准备结果。它标识一份工具选择，不是授权令牌。修改来源、工具目录、Schema 或选择可能使旧准备失效。MCP 目录在每次模型准备前刷新；调用时检查当前绑定，网络在检查后断开仍可能产生执行错误。

## 4. SDK、纯 HTTP 与 LangChain

框架无关 SDK：

```python
from context_protocol.gateway_agent import GatewayAgent

# model 接收类型化 ModelInput，返回 content 和 tool_calls。
agent = GatewayAgent(context, model)
answer = agent.run("分析项目")
```

LangChain 模型：

```python
from context_protocol.adapters.langchain import GatewayAgent

agent = GatewayAgent(context, langchain_chat_model)
answer = agent.run("分析项目")
```

这两个 Agent 都不接收工具执行器或 MCP 配置；也可自行实现循环，HTTP 契约完全相同。SDK 提供 context.call_tool(GatewayCallInput(...))，网络重试使用相同请求体。

完整无模型费用示例：

```powershell
# 先在工作台添加算术 MCP 来源并选择到实例上
.venv/Scripts/python.exe -m examples.gateway_agent --session INSTANCE_ID
node examples/gateway_agent.mjs http://127.0.0.1:8010/api INSTANCE_ID
```

## 服务器本地方法

```python
from context_protocol.app import create_app
from context_protocol.tools import LocalTool
from context_protocol.models.context import RegisteredTool

def multiply(a, b):
    return a*b

tool = LocalTool(RegisteredTool(name="multiply", description="计算整数乘积",
    input_schema={"type":"object","properties":{"a":{"type":"integer"},"b":{"type":"integer"}},
                  "required":["a","b"]}), multiply)
app = create_app(local_tool_providers={"arithmetic": [tool]})
```

然后添加 `{"transport":"local","registry_key":"arithmetic"}` 来源。HTTP 只接收描述和连接配置，不上传或反序列化函数代码。位于另一台电脑、JS 进程中的方法应包装成 MCP 服务接入。上下文能力使用 transport=context，在本实例内执行，Agent 不必注册 ContextTools。

## 执行重试与生命周期

同一 call_id、相同请求体的已完成调用直接返回原结果并标记 duplicate=true，不再次执行。异内容复用 call_id 返回 409。服务先持久记录 pending，再执行；超时、取消、崩溃后 pending 调用的结果可能不确定，重复请求返回 409，必须核实后人工恢复，不能宣称外部副作用恰好执行一次。

工具的确定性错误记录为 is_error=true；它不证明工具没有产生副作用。准备结果在服务重启后失效，但配置、历史与已完成调用结果保留。MCP 连接在专属常驻任务中维护，每个实例有独立会话；服务退出或 `/tools/disconnect` 会关闭连接，下一次 prepare 可重连。

当前集中网关运行在单个 Web 进程中，run.ps1 默认即为单进程；尚不支持多 worker 之间共享准备结果和连接。多 Agent 同时推进同一实例时仍需协调轮次。

来源管理可启动服务器命令并配置网络工具。当前服务默认仅监听本机且没有身份认证；来源管理面向可信的本地管理员，不能直接以现有配置开放到不可信网络。
