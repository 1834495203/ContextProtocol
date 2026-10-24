# 可选的上下文工具提供方

集中式模式下，在工作台添加 transport=context 的工具来源并选到模板或实例即可，由服务端接入提供方，Agent 无需注册。下文的宿主本地注册与独立 MCP 暴露继续可选，见 [服务端工具网关](tool-gateway.md)。

上下文管理命令与面向模型的工具是两层接口。`state.set`、`memory.append` 命令继续可通过 HTTP 使用；只有宿主显式接入上下文工具提供方后，相应能力才进入模型工具清单。

新模板、实例和 LangChain Agent 不会自动注入状态工具。核心编译器不导入执行器；上下文工具与其他本地/MCP 工具共享目录同步、宿主绑定、描述覆盖和执行前失效检查。

## 发现定义

`GET /api/context-tools` 无需数据库即可读取纯描述目录，包含 `registration_required: true`、建议的 provider_id 和 tools。`GET /api/protocol` 同时返回 context_tools 以及 context_tools_auto_register=false。发现定义不会安装函数、创建宿主绑定或调用模型。

| 可选能力 | 工具原名 | 本地模型别名 | 实现 |
| --- | --- | --- | --- |
| state.set | context.state.set | context_state_set | 提交 state.set 命令 |
| memory.append | context.memory.append | context_memory_append | 提交 memory.append 命令 |

默认提供方 ID 为 context，所以稳定工具身份为 `context/context.state.set`、`context/context.memory.append`。提供方身份与工具名称是独立字段；原名保留 context 前缀以兼容已有调用记录。MCP 接入可能生成不同的安全模型别名，但使用相同 provider_id 时稳定身份不变。

## 任意宿主：显式本地注册

```python
from context_protocol.tools import HostTools
from context_protocol.providers.context import ContextTools

with HostTools(context, "desktop") as host:
    provider = ContextTools(context, tools=("state.set", "memory.append"),
                            turn_id="current-turn")
    provider.register(host)
    prepared = host.prepare()
    if not prepared.ready:
        raise ValueError(prepared.issues)
    result = await host.call(prepared, "context_state_set", {
        "key": "phase", "value": "design", "value_type": "string"
    })
    host.unregister_provider("context")
```

实例由 ContextClient 固定，工具参数不允许选择另一个实例。turn_id 可传字符串或无参函数，用于宿主按轮次关联更新。ContextTools 默认只选择 state.set；构造它本身不注册，`tools=()` 选择空集合，未接入提供方则没有这些模型能力。

写状态和记忆返回业务结果，不自动写 tool.call/tool.result；宿主和其他工具一样负责记录调用历史。参数 Schema 和 Pydantic 业务校验均适用。撤销、禁用、租约失效后旧的准备结果不能再通过 HostTools 执行。

## LangChain：普通工具提供方

```python
from context_protocol.providers.context import ContextTools
from context_protocol.adapters.langchain import ContextAgent

provider = ContextTools(context, tools=("state.set", "memory.append"))
agent = ContextAgent(context, model, host_id="desktop", tools=business_tools,
                     tool_providers={"context": provider.tools})
try:
    answer = agent.run("分析任务并保存关键结论")
finally:
    agent.close()
```

`tool_providers` 接收普通 `LocalTool(definition, handler)` 列表，不依赖 ContextTools 的具体类。适配器只做通用框架转换和执行前检查；同步、异步提供方均可接入。模型名称冲突时明确拒绝，宿主需配置不同 model_name。

若需要更新关联当前轮次，可在构造提供方时传 `turn_id=lambda: agent.middleware.turn_id`；函数只在调用工具时执行。

## 独立 MCP 提供方

其他 Agent 可以直接连接下面的 stdio MCP Server，把上下文能力当作外部工具使用：

```powershell
.venv/Scripts/python.exe -m context_protocol.adapters.context_mcp `
  --api-url http://127.0.0.1:8010/api `
  --session EXISTING_CONTEXT_ID `
  --tools state.set memory.append
```

在 MCP 宿主配置中，把命令设为项目 Python 解释器，args 设为上述参数，cwd 设为项目根目录。服务固定连接一个已有实例，只暴露 --tools 选择的能力；内部复用同一个 ContextTools 实现，不包含模型调用或 Agent Loop。

该服务使用本项目可选 MCP 依赖，安装 `requirements-mcp.txt` 后可用。标准输出保留给 MCP 协议，不输出用户状态或日志。当前实现提供 stdio；如果需要网络 MCP，可由宿主包装或使用其网络部署方式。

## 描述、模板和旧数据

工作台可为稳定工具身份配置工具描述和参数解释，也可设为必需或禁用；与外部工具使用相同的 tool.prompt.set 配置。模板只保留需求和提示词，不携带执行器。

旧数据中 builtin=true 的自动注入定义作为兼容元数据保留，不进入可调用工具来源。宿主可以显式注册同名工具，或像普通工具一样注销；不再有状态工具的保留名称限制。旧调用记录保持原样，LangChain 在接入明确的提供方时使用其名称映射读取历史。

旧的实例级 tool.register 仍用于兼容；需要多环境隔离、撤销与租约检查时应使用宿主绑定。/api/protocol 的 state_tool 和 Python STATE_TOOL 常量仅保留为旧版描述，不代表存在已注册的执行器。
